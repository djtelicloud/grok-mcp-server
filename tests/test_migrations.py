# tests/test_migrations.py
# Migration-ordering integration guard. Rounds 1-3 each added store
# migrations (v6 routing_calibration, v7 knowledge, v8 caller metadata,
# v9 job request ids); this file pins that the user_version chain stays
# strictly sequential in the source, that a FRESH db lands exactly on the
# head version with the full table set, and that a db frozen at the v5
# schema (the round-2 baseline) upgrades cleanly to head with its data
# intact and every post-v5 surface usable.

import inspect
import re
import sqlite3

import pytest

from src.utils import GrokSessionStore

# The current schema head. Bump this alongside any new migration — the
# sequential-chain test below will fail loudly if the source and this pin
# ever disagree.
SCHEMA_HEAD = 18

# Every table a fully migrated store must carry (sqlite internals and the
# optional knowledge_fts shadow tables excluded — FTS5 availability is a
# build-time property covered by tests/test_knowledge.py).
EXPECTED_TABLES = {
    "sessions",
    "telemetry",
    "messages",
    "task_memory",
    "jobs",
    "routing_calibration",
    "knowledge",
    "workspace_evidence",
    "swarm_tasks",
    "swarm_candidates",
    "provider_attempts",
    "telemetry_attempts",
}


async def _fetch_version(store):
    async with store._conn.execute("PRAGMA user_version;") as cursor:
        row = await cursor.fetchone()
        return row[0]


async def _fetch_tables(store):
    async with store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table';"
    ) as cursor:
        return {r[0] for r in await cursor.fetchall()}


async def _fetch_columns(store, table):
    async with store._conn.execute(f"PRAGMA table_info({table});") as cursor:
        return {r[1] for r in await cursor.fetchall()}


def _build_v5_db(path):
    """Hand-build a db exactly as GrokSessionStore left it at user_version 5
    (post round-2, pre routing_calibration/knowledge/caller/request-id), with
    one seeded row per table so the upgrade's data preservation is provable."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE sessions (
                session_name TEXT PRIMARY KEY,
                cli_session_id TEXT,
                api_thread_id TEXT,
                last_active TEXT,
                model TEXT
            );
            CREATE TABLE telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent TEXT,
                chosen_plane TEXT,
                success INTEGER,
                latency REAL,
                cost REAL,
                context_id TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                metadata TEXT DEFAULT NULL,
                FOREIGN KEY(session_name) REFERENCES sessions(session_name) ON DELETE CASCADE
            );
            CREATE INDEX idx_messages_session_id ON messages(session_name, id ASC);
            CREATE INDEX idx_telemetry_intent ON telemetry(intent);
            CREATE INDEX idx_messages_timestamp ON messages(timestamp);
            CREATE INDEX idx_telemetry_context_id ON telemetry(context_id);
            CREATE TABLE task_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_hash TEXT,
                prompt_terms TEXT,
                prompt_excerpt TEXT,
                outcome_summary TEXT,
                plane TEXT,
                model TEXT,
                profile TEXT,
                success INTEGER,
                latency REAL,
                cost REAL,
                context_id TEXT,
                created_at TEXT,
                metadata TEXT DEFAULT NULL
            );
            CREATE INDEX idx_task_memory_hash ON task_memory(task_hash);
            CREATE INDEX idx_task_memory_context_id ON task_memory(context_id);
            CREATE INDEX idx_task_memory_created_at ON task_memory(created_at);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                prompt TEXT,
                model TEXT,
                created_at TEXT,
                updated_at TEXT,
                result TEXT,
                cost REAL DEFAULT 0.0
            );
            CREATE INDEX idx_jobs_created_at ON jobs(created_at);
            PRAGMA user_version = 5;
            """
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('legacy-sess', 'cli-1', 'thread-1', '2026-01-01T00:00:00', 'grok-4');"
        )
        conn.execute(
            "INSERT INTO telemetry (intent, chosen_plane, success, latency, cost, context_id) "
            "VALUES ('coding', 'API', 1, 0.5, 0.01, 'ctx-legacy');"
        )
        conn.execute(
            "INSERT INTO telemetry (intent, chosen_plane, success, latency, cost, context_id) "
            "VALUES ('history-compaction', 'API', 1, 0.1, 0.0, 'ctx-fold');"
        )
        conn.execute(
            "INSERT INTO telemetry (intent, chosen_plane, success, latency, cost, context_id) "
            "VALUES ('broken-route', 'API', 0, 0.1, 0.0, 'ctx-failure');"
        )
        conn.execute(
            "INSERT INTO messages (session_name, role, content, timestamp) "
            "VALUES ('legacy-sess', 'user', 'hello from v5', '2026-01-01T00:00:01');"
        )
        conn.execute(
            "INSERT INTO task_memory (task_hash, prompt_terms, prompt_excerpt, outcome_summary, "
            "plane, model, profile, success, latency, cost, context_id, created_at) "
            "VALUES ('h1', 'fix bug', 'fix the bug', 'done', 'API', 'grok-4', 'default', 1, "
            "0.5, 0.01, 'ctx-legacy', '2026-01-01T00:00:02');"
        )
        conn.execute(
            "INSERT INTO jobs (id, status, prompt, model, created_at, updated_at, result) "
            "VALUES ('job-legacy', 'done', 'old research', 'grok-4', "
            "'2026-01-01T00:00:03', '2026-01-01T00:00:04', 'old result');"
        )
        conn.commit()
    finally:
        conn.close()


class TestMigrationChain:
    def test_user_version_increments_are_sequential(self):
        """Drift guard: the migration assignments in _ensure_initialized must
        be exactly 1..SCHEMA_HEAD in order, and every `if version < N` gate
        must set PRAGMA user_version = N. Three separate stages added
        migrations this round — this is what keeps the next one honest."""
        source = inspect.getsource(GrokSessionStore._ensure_initialized)

        assignments = [
            int(m) for m in re.findall(r"PRAGMA user_version = (\d+);", source)
        ]
        assert assignments == list(range(1, SCHEMA_HEAD + 1)), (
            f"migration assignments must be sequential 1..{SCHEMA_HEAD}, got {assignments}"
        )

        gates = [int(m) for m in re.findall(r"if version < (\d+):", source)]
        assert gates == assignments, (
            f"every gate needs a matching version bump: gates={gates}, assignments={assignments}"
        )

    @pytest.mark.asyncio
    async def test_fresh_db_migrates_to_head(self, tmp_path):
        """A brand-new db runs the whole chain and lands exactly on head with
        the full table set and the post-v5 columns present."""
        store = GrokSessionStore(db_path=tmp_path / "fresh.db")
        try:
            await store._ensure_initialized()

            assert await _fetch_version(store) == SCHEMA_HEAD
            assert EXPECTED_TABLES <= await _fetch_tables(store)

            telemetry_cols = await _fetch_columns(store, "telemetry")
            assert {"metadata", "created_at", "context_id"} <= telemetry_cols
            jobs_cols = await _fetch_columns(store, "jobs")
            assert {"caller", "request_id"} <= jobs_cols
            task_memory_cols = await _fetch_columns(store, "task_memory")
            assert {
                "remote_file_id", "synced_at", "sync_attempts", "sync_error",
            } <= task_memory_cols
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_v5_db_upgrades_to_head_with_data_intact(self, tmp_path):
        """The round-2 baseline schema (user_version 5) reopened by current
        code: migrations 6-9 apply cleanly, seeded rows survive, and every
        post-v5 store surface works on the upgraded db."""
        db_path = tmp_path / "legacy_v5.db"
        _build_v5_db(db_path)

        store = GrokSessionStore(db_path=db_path)
        try:
            await store._ensure_initialized()

            # Chain completed.
            assert await _fetch_version(store) == SCHEMA_HEAD
            assert EXPECTED_TABLES <= await _fetch_tables(store)
            assert {"metadata", "created_at"} <= await _fetch_columns(store, "telemetry")
            assert {"caller", "request_id"} <= await _fetch_columns(store, "jobs")
            async with store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index';"
            ) as cursor:
                indexes = {r[0] for r in await cursor.fetchall()}
            assert {
                "idx_routing_calibration_updated_at",
                "idx_knowledge_scope",
                "idx_telemetry_created_at",
            } <= indexes

            # Pre-upgrade content survived. Unsupported legacy outcome labels
            # are deliberately withdrawn by v14; verified failures and the
            # mechanical compaction operation remain.
            messages = await store.load_messages("legacy-sess")
            assert [m["content"] for m in messages] == ["hello from v5"]
            job = await store.get_job("job-legacy")
            assert job["result"] == "old result"
            assert job["caller"] is None and job["request_id"] is None
            rows = await store.get_telemetry_stats()
            legacy = [r for r in rows if r["context_id"] == "ctx-legacy"]
            assert legacy and legacy[0]["metadata"] is None
            assert legacy[0]["success"] is None
            assert next(r for r in rows if r["context_id"] == "ctx-fold")["success"] == 1
            assert next(r for r in rows if r["context_id"] == "ctx-failure")["success"] == 0
            async with store._conn.execute(
                "SELECT success FROM task_memory WHERE task_hash = 'h1';"
            ) as cursor:
                assert (await cursor.fetchone())[0] is None

            # Post-v5 surfaces are live on the upgraded db.
            await store.upsert_routing_calibration(
                "coding", "fast", "grok-4", success_rate=1.0, avg_cost_usd=0.01, n=6
            )
            calib = await store.get_routing_calibration(max_age_hours=1)
            assert len(calib) == 1 and calib[0]["n"] == 6

            fact_id = await store.save_fact("the deploy target is cloud run")
            found = await store.search_facts("deploy target")
            assert any(f["id"] == fact_id for f in found)

            await store.save_telemetry(
                "coding", "API", 1, 0.2, 0.01,
                caller="claude-code", request_id="rid-upgrade",
            )
            await store.create_job(
                "job-new", "new research", "grok-4",
                caller="codex", request_id="rid-upgrade",
            )
            new_job = await store.get_job("job-new")
            assert new_job["caller"] == "codex"
            assert new_job["request_id"] == "rid-upgrade"
        finally:
            await store.close()


def _build_v9_db(path):
    """A db exactly as GrokSessionStore left it at user_version 9 (pre
    task-memory sync bookkeeping): the v5 baseline plus the v6-v9 shapes
    applied by hand, so the v10 upgrade's data preservation is provable."""
    _build_v5_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE routing_calibration (
                category TEXT NOT NULL,
                route TEXT NOT NULL,
                model TEXT NOT NULL,
                success_rate REAL NOT NULL DEFAULT 0.0,
                avg_cost_usd REAL NOT NULL DEFAULT 0.0,
                n INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                PRIMARY KEY (category, route, model)
            );
            CREATE INDEX idx_routing_calibration_updated_at
                ON routing_calibration(updated_at);
            CREATE TABLE knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL DEFAULT 'global',
                fact TEXT NOT NULL,
                source TEXT,
                terms TEXT,
                created_at TEXT,
                last_used_at TEXT,
                uses INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_knowledge_scope ON knowledge(scope);
            CREATE INDEX idx_knowledge_created_at ON knowledge(created_at);
            ALTER TABLE telemetry ADD COLUMN metadata TEXT DEFAULT NULL;
            ALTER TABLE telemetry ADD COLUMN created_at TEXT DEFAULT NULL;
            ALTER TABLE jobs ADD COLUMN caller TEXT DEFAULT NULL;
            CREATE INDEX idx_telemetry_created_at ON telemetry(created_at);
            ALTER TABLE jobs ADD COLUMN request_id TEXT DEFAULT NULL;
            PRAGMA user_version = 9;
            """
        )
        conn.commit()
    finally:
        conn.close()


class TestV10TaskMemorySync:
    @pytest.mark.asyncio
    async def test_v9_db_upgrades_to_v10_with_data_intact(self, tmp_path):
        """A db frozen at v9 gains the task-memory sync columns + partial
        index; the seeded row survives as an unsynced outbox entry and the
        new outbox surface works on the upgraded db."""
        db_path = tmp_path / "legacy_v9.db"
        _build_v9_db(db_path)

        store = GrokSessionStore(db_path=db_path)
        try:
            await store._ensure_initialized()

            assert await _fetch_version(store) == SCHEMA_HEAD
            assert {
                "remote_file_id", "synced_at", "sync_attempts", "sync_error",
            } <= await _fetch_columns(store, "task_memory")
            async with store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index';"
            ) as cursor:
                indexes = {r[0] for r in await cursor.fetchall()}
            assert "idx_task_memory_unsynced" in indexes

            # The pre-upgrade row IS the outbox: unsynced with zero attempts.
            rows = await store.list_unsynced_task_memories()
            assert len(rows) == 1
            row = rows[0]
            assert row["task_hash"] == "h1"
            assert row["synced_at"] is None
            assert row["sync_attempts"] == 0
            assert await store.count_unsynced_task_memories() == 1

            # Failure marking bumps attempts and stores a bounded error.
            await store.mark_task_memory_sync_failed(row["id"], "boom " * 400)
            failed = (await store.list_unsynced_task_memories())[0]
            assert failed["sync_attempts"] == 1
            assert len(failed["sync_error"]) < 600  # bounded to 500 + marker

            # max_attempts filter excludes exhausted rows.
            assert await store.list_unsynced_task_memories(max_attempts=1) == []

            # Success marking drains the outbox and maps the remote id back.
            await store.mark_task_memory_synced(row["id"], "file-abc")
            assert await store.count_unsynced_task_memories() == 0
            assert await store.list_unsynced_task_memories() == []
            mapped = await store.get_task_memories_by_remote_ids(["file-abc"])
            assert [m["id"] for m in mapped] == [row["id"]]
            assert mapped[0]["sync_error"] is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_v10_duplicate_column_rerun_is_benign(self, tmp_path):
        """A pre-existing task_memory.synced_at column (partial prior run)
        must not abort the v10 migration."""
        db_path = tmp_path / "v9_dup.db"
        _build_v9_db(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE task_memory ADD COLUMN synced_at TEXT DEFAULT NULL;")
            conn.commit()
        finally:
            conn.close()

        store = GrokSessionStore(db_path=db_path)
        try:
            await store._ensure_initialized()
            assert await _fetch_version(store) == SCHEMA_HEAD
            assert {
                "remote_file_id", "synced_at", "sync_attempts", "sync_error",
            } <= await _fetch_columns(store, "task_memory")
        finally:
            await store.close()


class TestV16TelemetryAttempts:
    @staticmethod
    async def _freeze_at_v15(path):
        store = GrokSessionStore(db_path=path)
        await store._ensure_initialized()
        await store.close()
        conn = sqlite3.connect(path)
        try:
            conn.execute("DROP TABLE telemetry_attempts;")
            conn.execute(
                "ALTER TABLE provider_attempts DROP COLUMN harvest_document_json;"
            )
            conn.execute(
                "ALTER TABLE provider_attempts DROP COLUMN harvest_document_digest;"
            )
            conn.execute(
                "ALTER TABLE provider_attempts DROP COLUMN harvest_episode_id;"
            )
            conn.execute("PRAGMA user_version = 15;")
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_v15_db_upgrades_through_v16_v17_and_v18(self, tmp_path):
        db_path = tmp_path / "legacy_v15.db"
        await self._freeze_at_v15(db_path)

        store = GrokSessionStore(db_path=db_path)
        try:
            await store._ensure_initialized()
            assert await _fetch_version(store) == 18
            assert await _fetch_columns(store, "telemetry_attempts") == {
                "id",
                "telemetry_id",
                "attempt_ordinal",
                "attempt_json",
                "attempt_digest",
            }
            async with store._conn.execute(
                "PRAGMA index_info(idx_telemetry_attempts_parent);"
            ) as cursor:
                assert [row[2] for row in await cursor.fetchall()] == [
                    "telemetry_id",
                    "attempt_ordinal",
                ]
            assert {
                "harvest_document_json",
                "harvest_document_digest",
                "harvest_episode_id",
            } <= await _fetch_columns(store, "provider_attempts")
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_v15_unknown_preexisting_table_is_not_certified(self, tmp_path):
        db_path = tmp_path / "foreign_v15.db"
        await self._freeze_at_v15(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE telemetry_attempts (id INTEGER);")
            conn.commit()
        finally:
            conn.close()

        store = GrokSessionStore(db_path=db_path)
        try:
            with pytest.raises(RuntimeError, match="predates v16"):
                await store._ensure_initialized()
            assert await _fetch_version(store) == 15
        finally:
            await store.close()


class TestV17ProviderAttemptCertification:
    @staticmethod
    async def _create_current(path):
        store = GrokSessionStore(db_path=path)
        await store._ensure_initialized()
        await store.close()

    @pytest.mark.asyncio
    async def test_current_head_rejects_foreign_provider_attempt_column(self, tmp_path):
        db_path = tmp_path / "foreign-v17-column.db"
        await self._create_current(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE provider_attempts ADD COLUMN foreign_data TEXT;")
            conn.commit()
        finally:
            conn.close()

        store = GrokSessionStore(db_path=db_path)
        try:
            with pytest.raises(RuntimeError, match="v17 columns"):
                await store._ensure_initialized()
            assert await _fetch_version(store) == 18
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_current_head_rejects_missing_provider_attempt_column(self, tmp_path):
        db_path = tmp_path / "missing-v17-column.db"
        await self._create_current(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "ALTER TABLE provider_attempts DROP COLUMN harvest_episode_id;"
            )
            conn.commit()
        finally:
            conn.close()

        store = GrokSessionStore(db_path=db_path)
        try:
            with pytest.raises(RuntimeError, match="v17 columns"):
                await store._ensure_initialized()
            assert await _fetch_version(store) == 18
        finally:
            await store.close()

    @pytest.mark.parametrize(
        ("index_name", "replacement_sql"),
        (
            (
                "idx_provider_attempts_request",
                "CREATE UNIQUE INDEX idx_provider_attempts_request "
                "ON provider_attempts(request_id) WHERE request_id IS NOT NULL;",
            ),
            (
                "idx_provider_attempts_harvest",
                "CREATE INDEX idx_provider_attempts_harvest "
                "ON provider_attempts(harvest_status, harvest_next_at, id) "
                "WHERE harvest_status != 'held';",
            ),
        ),
    )
    @pytest.mark.asyncio
    async def test_current_head_rejects_partial_required_index_without_mutation(
        self, tmp_path, index_name, replacement_sql
    ):
        db_path = tmp_path / f"partial-{index_name}.db"
        await self._create_current(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"DROP INDEX {index_name};")
            conn.execute(replacement_sql)
            conn.commit()
            index_sql_before = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()[0]
        finally:
            conn.close()

        store = GrokSessionStore(db_path=db_path)
        try:
            with pytest.raises(RuntimeError, match="v17 indexes"):
                await store._ensure_initialized()
        finally:
            await store.close()
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version;").fetchone()[0] == 18
            assert (
                conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = ?",
                    (index_name,),
                ).fetchone()[0]
                == index_sql_before
            )
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_future_schema_head_is_rejected_without_schema_mutation(self, tmp_path):
        db_path = tmp_path / "future-v19.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 19;")
            conn.commit()
            tables_before = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            conn.close()

        store = GrokSessionStore(db_path=db_path)
        try:
            with pytest.raises(RuntimeError, match="unsupported.*19"):
                await store._ensure_initialized()
            assert store._conn is None
            assert store._initialized is False
        finally:
            await store.close()
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version;").fetchone()[0] == 19
            assert {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            } == tables_before
        finally:
            conn.close()


class TestMigrationFailureAtomicity:
    """The v8/v9 gates must match the try/rollback discipline of the earlier
    migrations: a mid-migration failure rolls the transaction back, never
    stamps the new user_version, and leaves the store recoverable (round-3
    review finding — the original v8 block swallowed non-duplicate-column
    ALTER failures and could stamp v8 with columns missing)."""

    @staticmethod
    def _build_v7_db(path, drop_jobs=False, pre_add_metadata=False):
        """A db stamped user_version=7. drop_jobs simulates a corrupt/foreign
        db that makes v8's jobs ALTER fail with a NON-duplicate-column
        OperationalError; pre_add_metadata simulates a column that already
        exists (the benign duplicate-column rerun)."""
        _build_v5_db(path)
        conn = sqlite3.connect(path)
        try:
            if drop_jobs:
                conn.execute("DROP TABLE jobs;")
            if pre_add_metadata:
                conn.execute("ALTER TABLE telemetry ADD COLUMN metadata TEXT DEFAULT NULL;")
            conn.execute("PRAGMA user_version = 7;")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _raw_version(path):
        conn = sqlite3.connect(path)
        try:
            return conn.execute("PRAGMA user_version;").fetchone()[0]
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_v8_failure_rolls_back_and_store_stays_recoverable(self, tmp_path):
        db_path = tmp_path / "v7_broken.db"
        self._build_v7_db(db_path, drop_jobs=True)

        store = GrokSessionStore(db_path=db_path)
        try:
            with pytest.raises(Exception, match="(?i)no such table"):
                await store._ensure_initialized()

            # Nothing half-committed: version stays 7, telemetry untouched.
            assert self._raw_version(db_path) == 7
            conn = sqlite3.connect(db_path)
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(telemetry);")}
            finally:
                conn.close()
            assert "metadata" not in cols and "created_at" not in cols

            # No dangling transaction: repairing the db lets the SAME store
            # instance initialize cleanly (not 'cannot start a transaction
            # within a transaction').
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL "
                    "DEFAULT 'queued', prompt TEXT, model TEXT, created_at TEXT, "
                    "updated_at TEXT, result TEXT, cost REAL DEFAULT 0.0);"
                )
                conn.commit()
            finally:
                conn.close()
            await store._ensure_initialized()
            assert await _fetch_version(store) == SCHEMA_HEAD
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_v8_duplicate_column_rerun_is_benign(self, tmp_path):
        """A pre-existing telemetry.metadata column (duplicate-column ALTER)
        must not abort the migration — only real DDL failures do."""
        db_path = tmp_path / "v7_dup.db"
        self._build_v7_db(db_path, pre_add_metadata=True)

        store = GrokSessionStore(db_path=db_path)
        try:
            await store._ensure_initialized()
            assert await _fetch_version(store) == SCHEMA_HEAD
            assert {"metadata", "created_at"} <= await _fetch_columns(store, "telemetry")
            assert {"caller", "request_id"} <= await _fetch_columns(store, "jobs")
        finally:
            await store.close()
