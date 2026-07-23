from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import local_plane_loader

SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
TERM_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")
TENANT_SCOPE_PATTERN = re.compile(r"^(tenant-[0-9a-f]{24}):")
TENANT_SCOPE_GLOB = "tenant-" + "[0-9a-f]" * 24 + ":*"
DURABLE_TEXT_MAX_BYTES = 100_000

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?im)\b((?:xai|openai|anthropic|claude|gemini|google|github|gh|aws|"
            r"azure|npm|pypi)[A-Z0-9_ -]*(?:api[_ -]?key|token|secret|password))"
            r"\s*[:=]\s*([^\s,;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r"(?im)\b((?:[A-Z][A-Z0-9_ -]{0,80}?)?(?:API[_ -]?KEY|ACCESS[_ -]?KEY|"
            r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL))\s*[:=]\s*([^\s,;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"\b(?:xai-|sk-(?:proj-)?|gh[opusr]_)[A-Za-z0-9_-]{12,}\b"),
        "[REDACTED_TOKEN]",
    ),
    (
        re.compile(
            r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
)

_SECRET_KEY_NAME = re.compile(
    r"(?i)(?:^|[_ -])(?:api[_ -]?key|xai[_ -]?key|access[_ -]?key|auth[_ -]?token|"
    r"access[_ -]?token|refresh[_ -]?token|id[_ -]?token|bearer[_ -]?token|"
    r"token|secret|client[_ -]?secret|password|passwd|credential|credentials|"
    r"private[_ -]?key|authorization|cookie|set[_ -]?cookie)$"
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def redact_secrets(value: Any) -> str:
    text = str(value or "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_json_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact structured payloads without destroying their shape."""
    normalized_key = (
        str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")
    )
    if (
        normalized_key == "continue_token"
        and isinstance(value, str)
        and re.fullmatch(r"[0-9a-fA-F]{32}", value)
    ):
        return value
    if key and _SECRET_KEY_NAME.search(str(key)):
        return None if value is None else "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact_json_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_json_value(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def bounded_redacted_text(value: Any, *, max_bytes: int = DURABLE_TEXT_MAX_BYTES) -> str:
    """Redact and cap durable human-readable text on a UTF-8 boundary."""
    text = redact_secrets(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    marker = "\n…[truncated]"
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return marker_bytes[:max_bytes].decode("utf-8", errors="ignore")
    cut = encoded[: max_bytes - len(marker_bytes)]
    while cut:
        try:
            return cut.decode("utf-8") + marker
        except UnicodeDecodeError:
            cut = cut[:-1]
    return marker.lstrip()


def _durable_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = redact_json_value(payload)
    if not isinstance(safe, dict):
        return {}
    mission = safe.get("mission")
    if isinstance(mission, dict) and mission.get("protocol") == "unigrok_mission_v2":
        for field in ("text", "proposed_text", "review"):
            if field in safe:
                safe[field] = bounded_redacted_text(safe[field])
    return safe


def _retention_cutoff() -> str:
    raw = os.environ.get("UNIGROK_STATE_RETENTION_HOURS", "24")
    try:
        hours = int(raw)
    except ValueError:
        hours = 24
    hours = max(1, min(hours, 24 * 30))
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def normalize_session(value: Any) -> str:
    session = str(value or "").strip()
    if not SESSION_PATTERN.fullmatch(session):
        raise ValueError(
            "session must be 1-128 characters using letters, numbers, '.', '_', ':', '/', or '-'"
        )
    return session


def normalize_scope(value: Any) -> str:
    scope = str(value or "global").strip() or "global"
    if not SCOPE_PATTERN.fullmatch(scope):
        raise ValueError(
            "scope must be 1-128 characters using letters, numbers, '.', '_', ':', '/', or '-'"
        )
    return scope


def _terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.lower() for match in TERM_PATTERN.findall(value)))


class PublicStateStore:
    """Small local SQLite store for public sessions and distilled facts.

    Connections are short-lived and every write is a single transaction. This
    keeps the public service resilient across container restarts without
    inheriting any IDE, Git, shell, or provider state.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        configured = (
            path
            or os.environ.get("UNIGROK_STATE_PATH")
            or (
                Path(os.environ["UNIGROK_STATE_DIR"]) / "public-state.db"
                if os.environ.get("UNIGROK_STATE_DIR")
                else None
            )
            or (Path.home() / ".local" / "share" / "unigrok" / "public-state.db")
        )
        self.path = Path(configured).expanduser()
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _prune_retention_connection(connection: sqlite3.Connection) -> int:
        cutoff = _retention_cutoff()
        deleted = 0
        for statement in (
            "DELETE FROM agent_jobs WHERE created_at < ? "
            "AND status IN ('complete','error')",
            "DELETE FROM autonomy_jobs WHERE updated_at < ? "
            "AND status IN ('committed','terminal')",
            "DELETE FROM missions WHERE updated_at < ? "
            "AND status IN ('complete','failed','budget_exhausted','cancelled')",
            "DELETE FROM telemetry WHERE created_at < ?",
        ):
            cursor = connection.execute(statement, (cutoff,))
            deleted += max(0, int(cursor.rowcount))
        return deleted

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    model TEXT,
                    plane TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_name TEXT NOT NULL REFERENCES sessions(name) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT
                );
                CREATE INDEX IF NOT EXISTS messages_session_id
                    ON messages(session_name, id);
                CREATE TABLE IF NOT EXISTS session_commits (
                    job_id TEXT PRIMARY KEY,
                    session_name TEXT NOT NULL
                        REFERENCES sessions(name) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_packs (
                    session_name TEXT PRIMARY KEY
                        REFERENCES sessions(name) ON DELETE CASCADE,
                    pack_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    fact TEXT NOT NULL,
                    terms TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    uses INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(scope, fact)
                );
                CREATE INDEX IF NOT EXISTS knowledge_scope_id
                    ON knowledge(scope, id DESC);
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    request_kind TEXT NOT NULL,
                    route TEXT,
                    requested_plane TEXT,
                    resolved_plane TEXT,
                    model TEXT,
                    success INTEGER,
                    verified INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    fallback_reason TEXT,
                    stop_reason TEXT,
                    metadata TEXT
                );
                CREATE INDEX IF NOT EXISTS telemetry_created_at
                    ON telemetry(created_at DESC);
                CREATE INDEX IF NOT EXISTS telemetry_caller_created
                    ON telemetry(caller, created_at DESC);
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT,
                    owner TEXT
                );
                CREATE TABLE IF NOT EXISTS autonomy_jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    acceptance_hash TEXT NOT NULL,
                    acceptance_text TEXT NOT NULL,
                    continue_token TEXT NOT NULL UNIQUE,
                    claim_lease TEXT,
                    claim_expires_at TEXT,
                    ledger_cursor INTEGER NOT NULL DEFAULT 0,
                    request_json TEXT
                );
                CREATE INDEX IF NOT EXISTS autonomy_jobs_token
                    ON autonomy_jobs(continue_token);
                CREATE TABLE IF NOT EXISTS autonomy_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES autonomy_jobs(job_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS autonomy_ledger_job
                    ON autonomy_ledger(job_id, id);
                CREATE TABLE IF NOT EXISTS autonomy_artifacts (
                    hash TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES autonomy_jobs(job_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    acceptance_hash TEXT NOT NULL,
                    acceptance_text TEXT NOT NULL,
                    continue_token TEXT NOT NULL UNIQUE,
                    package_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    checkpoint_version INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_generation INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at TEXT,
                    envelope_version INTEGER NOT NULL DEFAULT 1,
                    verify_failures INTEGER NOT NULL DEFAULT 0,
                    ledger_cursor INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS missions_status
                    ON missions(status, updated_at);
                CREATE TABLE IF NOT EXISTS mission_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_ledger_mission
                    ON mission_ledger(mission_id, id);
                CREATE TABLE IF NOT EXISTS mission_artifacts (
                    hash TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    sealed TEXT NOT NULL,
                    projection TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mission_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    class TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    artifact_refs TEXT NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(mission_id, digest)
                );
                CREATE TABLE IF NOT EXISTS mission_side_effects (
                    quantum_key TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    receipt_json TEXT NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            self._ensure_local_plane_schema_sync(connection)
            self._seed_local_plane_sync(connection)

            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(autonomy_jobs)").fetchall()
            }
            if columns and "request_json" not in columns:
                connection.execute(
                    "ALTER TABLE autonomy_jobs ADD COLUMN request_json TEXT"
                )
            agent_job_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(agent_jobs)").fetchall()
            }
            if agent_job_columns and "owner" not in agent_job_columns:
                connection.execute("ALTER TABLE agent_jobs ADD COLUMN owner TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS agent_jobs_owner "
                "ON agent_jobs(owner, job_id)"
            )
            self._prune_retention_connection(connection)
            connection.commit()

    def _ensure_local_plane_schema_sync(self, connection: sqlite3.Connection) -> None:
        schema_path = Path(__file__).with_name("schema_local_plane.sql")
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        defaults: list[tuple[str, Any]] = [
            ("local_concurrency_budget", 2),
            ("breaker_429", {"n": 5, "window_s": 60, "half_open_s": 30}),
            ("continue_max_per_job_on_shed", 3),
            ("catalog_ttl_s", 60),
        ]
        for key, value in defaults:
            connection.execute(
                """
                INSERT OR IGNORE INTO local_plane_knobs (key, value_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, json.dumps(value), utc_now()),
            )

    def _seed_local_plane_sync(self, connection: sqlite3.Connection) -> None:
        """Load optional local-plane seed data + automatic offline min-role dogfood."""
        seed_dir = Path(
            os.environ.get("UNIGROK_LOCAL_SEED_DIR")
            or (Path(__file__).parent / "data" / "local_plane")
        )
        if seed_dir.is_dir():
            try:
                local_plane_loader.load_seed_assets(
                    connection,
                    dialect_matrix_path=seed_dir / "dialect_matrix.json",
                    family_map_path=seed_dir / "family_map.json",
                    scorecard_path=seed_dir / "scorecard.json",
                    gate_manifest_path=seed_dir / "gate_manifest.json",
                    promote_path=seed_dir / "promote.json",
                    traps_path=seed_dir / "traps.json",
                )
            except (sqlite3.Error, OSError, ValueError):
                pass
        # Automatic: fund router+text_generator when empty so local plane can arm
        # whenever DMR is reachable (no manual per-seat SQL).
        try:
            local_plane_loader.ensure_dogfood_min_roles(connection)
        except (sqlite3.Error, AttributeError, ValueError):
            pass

    async def local_knob(self, key: str, default: Any = None) -> Any:
        def operation() -> Any:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value_json FROM local_plane_knobs WHERE key = ?",
                    (key,),
                ).fetchone()
            if row is None:
                return default
            try:
                return json.loads(str(row["value_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                return default

        return await self._read(operation)

    async def rewrite_local_binds(self, discovered: list[dict[str, Any]]) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            models = [
                local_plane_loader.DiscoveredModel(
                    model_id=str(item["model_id"]),
                    raw_name=str(item.get("raw_name") or item["model_id"]),
                    runtime=str(item.get("runtime") or "other"),
                    adapters=tuple(item.get("adapters") or ()),
                )
                for item in discovered or []
                if isinstance(item, dict) and item.get("model_id")
            ]
            with self._connect() as connection:
                report = local_plane_loader.rewrite_at_load(connection, models)
            return {
                "ok": report.ok,
                "ready_candidate": report.ready_candidate,
                "missing_min_roles": list(report.missing_min_roles),
                "errors": list(report.errors),
                "binds": [
                    {
                        "model_id": bind.model_id,
                        "role": bind.role,
                        "family": bind.family,
                        "metric_id": bind.metric_id,
                        "cert_id": bind.cert_id,
                    }
                    for bind in report.binds
                ],
            }

        return dict(await self._write(operation))

    async def local_data_ready(self) -> bool:
        def operation() -> bool:
            with self._connect() as connection:
                return bool(local_plane_loader.plane_data_ready(connection))

        return bool(await self._read(operation))

    async def local_bind(self, model_id: str, role: str) -> dict[str, Any] | None:
        def operation() -> dict[str, Any] | None:
            with self._connect() as connection:
                bind = local_plane_loader.get_bind(connection, model_id, role)
            if bind is None:
                return None
            return {
                "model_id": bind.model_id,
                "role": bind.role,
                "family": bind.family,
                "metric_id": bind.metric_id,
                "cert_id": bind.cert_id,
            }

        return await self._read(operation)

    async def local_binds(
        self,
        model_id: str | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            with self._connect() as connection:
                binds = local_plane_loader.list_binds(
                    connection, model_id=model_id, role=role
                )
            return [
                {
                    "model_id": bind.model_id,
                    "role": bind.role,
                    "family": bind.family,
                    "metric_id": bind.metric_id,
                    "cert_id": bind.cert_id,
                }
                for bind in binds
            ]

        return list(await self._read(operation))

    async def local_seed_assets(self, seed_dir: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            base = Path(seed_dir)
            with self._connect() as connection:
                stats = local_plane_loader.load_seed_assets(
                    connection,
                    dialect_matrix_path=base / "dialect_matrix.json",
                    family_map_path=base / "family_map.json",
                    scorecard_path=base / "scorecard.json",
                    gate_manifest_path=base / "gate_manifest.json",
                    promote_path=base / "promote.json",
                    traps_path=base / "traps.json",
                )
            return {
                "errors": list(stats.errors),
                "counts": {
                    "family_map": stats.family_map,
                    "dialect_profiles": stats.dialect_profiles,
                    "gate_manifest": stats.gate_manifest,
                    "promote": stats.promote,
                    "traps": stats.traps,
                    "scorecard": stats.scorecard,
                },
            }

        return dict(await self._write(operation))

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def _read(self, operation: Callable[..., Any], *args: Any) -> Any:
        await self.initialize()
        return await asyncio.to_thread(operation, *args)

    async def _write(self, operation: Callable[..., Any], *args: Any) -> Any:
        await self.initialize()
        async with self._write_lock:
            return await asyncio.to_thread(operation, *args)

    def _prune_retention_sync(self) -> int:
        with self._connect() as connection:
            deleted = self._prune_retention_connection(connection)
            connection.commit()
        return deleted

    async def prune_retention(self) -> int:
        return int(await self._write(self._prune_retention_sync))

    def _health_sync(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    async def health(self) -> bool:
        try:
            return bool(await self._read(self._health_sync))
        except (OSError, sqlite3.Error):
            return False

    def _append_turn_sync(
        self,
        session: str,
        user_text: str,
        assistant_text: str,
        model: str | None,
        plane: str | None,
        metadata: dict[str, Any] | None,
        commit_key: str | None,
    ) -> tuple[int, bool]:
        now = utc_now()
        safe_user = bounded_redacted_text(user_text).strip()
        safe_assistant = bounded_redacted_text(assistant_text).strip()
        safe_metadata = (
            json.dumps(redact_json_value(metadata), separators=(",", ":"))
            if metadata
            else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(name, created_at, updated_at, model, plane)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    model=COALESCE(excluded.model, sessions.model),
                    plane=COALESCE(excluded.plane, sessions.plane)
                """,
                (session, now, now, model, plane),
            )
            if commit_key:
                claimed = connection.execute(
                    "INSERT INTO session_commits(job_id, session_name, created_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(job_id) DO NOTHING",
                    (commit_key, session, now),
                )
                if int(claimed.rowcount) != 1:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM messages WHERE session_name=?", (session,)
                    ).fetchone()
                    connection.commit()
                    return int(row[0] if row else 0), False
            connection.executemany(
                """
                INSERT INTO messages(session_name, role, content, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (session, "user", safe_user, now, None),
                    (session, "assistant", safe_assistant, now, safe_metadata),
                ),
            )
            row = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_name=?", (session,)
            ).fetchone()
            connection.commit()
        return int(row[0] if row else 0), True

    async def append_turn(
        self,
        session: str,
        user_text: str,
        assistant_text: str,
        *,
        model: str | None = None,
        plane: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        count, _ = await self._write(
            self._append_turn_sync,
            normalize_session(session),
            user_text,
            assistant_text,
            model,
            plane,
            metadata,
            None,
        )
        return int(count)

    async def append_turn_once(
        self,
        session: str,
        user_text: str,
        assistant_text: str,
        *,
        commit_key: str,
        model: str | None = None,
        plane: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[int, bool]:
        key = str(commit_key or "").strip()
        if not key:
            raise ValueError("commit_key is required")
        count, inserted = await self._write(
            self._append_turn_sync,
            normalize_session(session),
            user_text,
            assistant_text,
            model,
            plane,
            metadata,
            key,
        )
        return int(count), bool(inserted)

    def _load_messages_sync(self, session: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at, metadata FROM (
                    SELECT id, role, content, created_at, metadata
                    FROM messages WHERE session_name=? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (session, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata: dict[str, Any] | None = None
            if row["metadata"]:
                try:
                    parsed = json.loads(row["metadata"])
                    metadata = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    metadata = None
            result.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "metadata": metadata,
                }
            )
        return result

    async def load_messages(self, session: str, limit: int = 24) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit or 24), 100))
        return list(await self._read(self._load_messages_sync, normalize_session(session), bounded))

    def _save_context_pack_sync(
        self, session: str, pack: dict[str, Any], version: int
    ) -> None:
        now = utc_now()
        payload = json.dumps(
            redact_json_value(pack), separators=(",", ":"), default=str
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO context_packs(session_name, pack_json, version, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_name) DO UPDATE SET
                    pack_json=excluded.pack_json,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (session, payload, int(version), now),
            )
            connection.commit()

    async def save_context_pack(
        self, session: str, pack: dict[str, Any], *, version: int = 1
    ) -> None:
        await self._write(
            self._save_context_pack_sync,
            normalize_session(session),
            pack,
            int(version),
        )

    def _load_context_pack_sync(self, session: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT pack_json, version FROM context_packs WHERE session_name=?",
                (session,),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["pack_json"] or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        data["version"] = int(row["version"] or data.get("version") or 1)
        return data

    async def load_context_pack(self, session: str) -> dict[str, Any] | None:
        return await self._read(
            self._load_context_pack_sync, normalize_session(session)
        )

    def _list_sessions_sync(
        self, limit: int, prefix: str | None
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if prefix:
                rows = connection.execute(
                    """
                    SELECT s.name, s.created_at, s.updated_at, s.model, s.plane,
                           COUNT(m.id) AS message_count
                    FROM sessions s LEFT JOIN messages m ON m.session_name=s.name
                    WHERE s.name LIKE ?
                    GROUP BY s.name ORDER BY s.updated_at DESC LIMIT ?
                    """,
                    (f"{prefix}%", limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT s.name, s.created_at, s.updated_at, s.model, s.plane,
                           COUNT(m.id) AS message_count
                    FROM sessions s LEFT JOIN messages m ON m.session_name=s.name
                    GROUP BY s.name ORDER BY s.updated_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    async def list_sessions(
        self, limit: int = 50, *, prefix: str | None = None
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit or 50), 100))
        safe_prefix = str(prefix or "")[:40] or None
        return list(await self._read(self._list_sessions_sync, bounded, safe_prefix))

    def _delete_session_sync(self, session: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE name=?", (session,))
            connection.commit()
        return bool(cursor.rowcount)

    async def delete_session(self, session: str) -> bool:
        return bool(await self._write(self._delete_session_sync, normalize_session(session)))

    def _save_fact_sync(self, fact: str, scope: str, source: str) -> int:
        now = utc_now()
        terms = " ".join(_terms(fact))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO knowledge(scope, fact, terms, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, fact) DO UPDATE SET source=excluded.source
                """,
                (scope, fact, terms, source, now),
            )
            row = connection.execute(
                "SELECT id FROM knowledge WHERE scope=? AND fact=?", (scope, fact)
            ).fetchone()
            connection.commit()
        return int(row[0])

    async def save_fact(self, fact: str, *, scope: str = "global", source: str = "manual") -> int:
        safe_fact = redact_secrets(fact).strip()
        if not safe_fact:
            raise ValueError("fact must not be empty")
        if len(safe_fact) > 4_000:
            raise ValueError("fact exceeds the 4000 character limit")
        return int(
            await self._write(
                self._save_fact_sync,
                safe_fact,
                normalize_scope(scope),
                str(source or "manual")[:160],
            )
        )

    def _search_facts_sync(self, query: str, scope: str | None, limit: int) -> list[dict[str, Any]]:
        query_terms = set(_terms(query))
        if not query_terms:
            return []
        with self._connect() as connection:
            tenant_match = TENANT_SCOPE_PATTERN.match(scope or "")
            tenant_global = (
                f"{tenant_match.group(1)}:global" if tenant_match is not None else None
            )
            if scope and tenant_global and scope != tenant_global:
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope IN (?, ?) "
                    "ORDER BY id DESC LIMIT 400",
                    (tenant_global, scope),
                ).fetchall()
            elif scope and tenant_global:
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope=? ORDER BY id DESC LIMIT 400",
                    (tenant_global,),
                ).fetchall()
            elif scope and scope != "global":
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope IN ('global', ?) "
                    "ORDER BY id DESC LIMIT 400",
                    (scope,),
                ).fetchall()
            elif scope:
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope='global' ORDER BY id DESC LIMIT 400"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope NOT GLOB ? "
                    "ORDER BY id DESC LIMIT 400",
                    (TENANT_SCOPE_GLOB,),
                ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            row_terms = set(str(row["terms"] or "").split())
            overlap = len(query_terms & row_terms)
            if overlap <= 0:
                continue
            item = dict(row)
            item["score"] = overlap / max(len(query_terms), 1)
            scored.append(item)
        scored.sort(key=lambda item: (float(item["score"]), int(item["id"])), reverse=True)
        return scored[:limit]

    async def search_facts(
        self, query: str, *, scope: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if not text:
            raise ValueError("query must not be empty")
        bounded = max(1, min(int(limit or 5), 25))
        scope_value = normalize_scope(scope) if scope else None
        return list(await self._read(self._search_facts_sync, text, scope_value, bounded))

    def _touch_facts_sync(self, fact_ids: list[int]) -> None:
        if not fact_ids:
            return
        now = utc_now()
        with self._connect() as connection:
            connection.executemany(
                "UPDATE knowledge SET last_used_at=?, uses=uses+1 WHERE id=?",
                ((now, fact_id) for fact_id in fact_ids),
            )
            connection.commit()

    async def touch_facts(self, fact_ids: list[int]) -> None:
        ids = list(dict.fromkeys(int(value) for value in fact_ids))
        await self._write(self._touch_facts_sync, ids)

    def _count_facts_sync(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM knowledge").fetchone()
        return int(row[0])

    async def count_facts(self) -> int:
        return int(await self._read(self._count_facts_sync))

    def _delete_fact_sync(self, fact_id: int, scope_prefix: str | None) -> bool:
        with self._connect() as connection:
            if scope_prefix:
                cursor = connection.execute(
                    "DELETE FROM knowledge WHERE id=? AND scope LIKE ?",
                    (fact_id, f"{scope_prefix}%"),
                )
            else:
                cursor = connection.execute("DELETE FROM knowledge WHERE id=?", (fact_id,))
            connection.commit()
        return bool(cursor.rowcount)

    async def delete_fact(self, fact_id: int, *, scope_prefix: str | None = None) -> bool:
        return bool(
            await self._write(
                self._delete_fact_sync,
                int(fact_id),
                str(scope_prefix or "")[:40] or None,
            )
        )

    def _save_telemetry_sync(self, row: dict[str, Any]) -> int:
        safe_metadata = json.dumps(
            redact_json_value(row.get("metadata") or {}),
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO telemetry(
                    created_at, caller, request_kind, route, requested_plane,
                    resolved_plane, model, success, verified, latency_ms,
                    cost_usd, fallback_reason, stop_reason, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    str(row.get("caller") or "anonymous")[:160],
                    str(row.get("request_kind") or "agent")[:80],
                    str(row.get("route") or "")[:80] or None,
                    str(row.get("requested_plane") or "")[:24] or None,
                    str(row.get("resolved_plane") or "")[:24] or None,
                    str(row.get("model") or "")[:160] or None,
                    None if row.get("success") is None else int(bool(row.get("success"))),
                    int(bool(row.get("verified"))),
                    max(0, int(row.get("latency_ms") or 0)),
                    max(0.0, float(row.get("cost_usd") or 0.0)),
                    redact_secrets(row.get("fallback_reason") or "")[:120] or None,
                    redact_secrets(row.get("stop_reason") or "")[:80] or None,
                    safe_metadata,
                ),
            )
            self._prune_retention_connection(connection)
            connection.commit()
        return int(cursor.lastrowid)

    async def save_telemetry(self, row: dict[str, Any]) -> int:
        return int(await self._write(self._save_telemetry_sync, dict(row)))

    def _get_caller_cost_today_sync(self, caller: str, day_start: str) -> float:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM telemetry "
                "WHERE caller=? AND created_at>=?",
                (caller, day_start),
            ).fetchone()
        return float(row[0] if row and row[0] is not None else 0.0)

    async def get_caller_cost_today(self, caller: str) -> float:
        """Return this UTC day's exact-principal telemetry spend."""
        normalized = str(caller or "").strip()
        if not normalized or len(normalized) > 160:
            raise ValueError("caller must be 1-160 characters")
        day_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        return float(
            await self._read(
                self._get_caller_cost_today_sync,
                normalized,
                day_start,
            )
        )

    def _reclassify_telemetry_error_sync(
        self,
        telemetry_id: int,
        latency_ms: int,
        fallback_reason: str,
        error_type: str,
        incurred_attempts: list[dict[str, Any]],
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata FROM telemetry WHERE id=?", (telemetry_id,)
            ).fetchone()
            if row is None:
                return False
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["post_provider_error"] = {
                "error_type": str(error_type or "Error")[:160],
                "incurred_attempts": incurred_attempts,
            }
            connection.execute(
                """
                UPDATE telemetry
                SET route='error', success=0, verified=1, latency_ms=?,
                    fallback_reason=?, stop_reason='error', metadata=?
                WHERE id=?
                """,
                (
                    max(0, int(latency_ms)),
                    redact_secrets(fallback_reason or "")[:120] or None,
                    json.dumps(
                        redact_json_value(metadata),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    telemetry_id,
                ),
            )
            connection.commit()
        return True

    async def reclassify_telemetry_error(
        self,
        telemetry_id: int,
        *,
        latency_ms: int,
        fallback_reason: str,
        error_type: str,
        incurred_attempts: list[dict[str, Any]],
    ) -> bool:
        return bool(
            await self._write(
                self._reclassify_telemetry_error_sync,
                int(telemetry_id),
                int(latency_ms),
                fallback_reason,
                error_type,
                incurred_attempts,
            )
        )

    def _save_agent_job_sync(
        self,
        job_id: str,
        status: str,
        payload: str | None,
        owner: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_jobs (job_id, created_at, status, payload, owner) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
                "created_at=excluded.created_at, status=excluded.status, "
                "payload=excluded.payload, owner=COALESCE(agent_jobs.owner, excluded.owner)",
                (job_id, utc_now(), status, payload, owner),
            )
            self._prune_retention_connection(connection)
            connection.commit()

    async def save_agent_job(
        self,
        job_id: str,
        status: str,
        payload: dict[str, Any] | None = None,
        *,
        owner: str | None = None,
    ) -> None:
        encoded = (
            json.dumps(
                _durable_agent_payload(payload), separators=(",", ":"), default=str
            )
            if payload is not None
            else None
        )
        await self._write(
            self._save_agent_job_sync,
            job_id,
            status,
            encoded,
            str(owner or "")[:160] or None,
        )

    def _load_agent_job_sync(
        self, job_id: str, owner: str | None
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            if owner:
                row = connection.execute(
                    "SELECT status, payload, owner FROM agent_jobs "
                    "WHERE job_id=? AND owner=?",
                    (job_id, owner),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT status, payload, owner FROM agent_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
        if row is None:
            return None
        payload: dict[str, Any] | None = None
        if row["payload"]:
            try:
                decoded = json.loads(row["payload"])
                payload = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                payload = None
        return {
            "status": str(row["status"]),
            "payload": payload,
        }

    async def load_agent_job(
        self, job_id: str, *, owner: str | None = None
    ) -> dict[str, Any] | None:
        return await self._read(
            self._load_agent_job_sync,
            job_id,
            str(owner or "")[:160] or None,
        )

    def _mirror_mission_result_sync(
        self,
        mission_id: str,
        expect_status: str,
        expect_checkpoint_version: int,
        expect_lease_generation: int,
        job_id: str,
        job_status: str,
        autonomy_status: str,
        encoded_payload: str,
    ) -> bool:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            mission = connection.execute(
                "SELECT status, job_id, checkpoint_version, lease_generation "
                "FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if (
                mission is None
                or str(mission["status"] or "") != expect_status
                or str(mission["job_id"] or "") != job_id
                or int(mission["checkpoint_version"] or 0)
                != int(expect_checkpoint_version)
                or int(mission["lease_generation"] or 0)
                != int(expect_lease_generation)
            ):
                connection.rollback()
                return False
            connection.execute(
                "INSERT INTO agent_jobs(job_id, created_at, status, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
                "created_at=excluded.created_at, status=excluded.status, "
                "payload=excluded.payload",
                (job_id, now, job_status, encoded_payload),
            )
            connection.execute(
                "UPDATE autonomy_jobs SET status=?, updated_at=? WHERE job_id=?",
                (autonomy_status, now, job_id),
            )
            self._prune_retention_connection(connection)
            connection.commit()
        return True

    async def mirror_mission_result(
        self,
        mission_id: str,
        *,
        expect_status: str,
        expect_checkpoint_version: int,
        expect_lease_generation: int,
        job_id: str,
        job_status: str,
        autonomy_status: str,
        payload: dict[str, Any],
    ) -> bool:
        encoded = json.dumps(
            _durable_agent_payload(payload), separators=(",", ":"), default=str
        )
        return bool(
            await self._write(
                self._mirror_mission_result_sync,
                mission_id,
                expect_status,
                int(expect_checkpoint_version),
                int(expect_lease_generation),
                job_id,
                job_status,
                autonomy_status,
                encoded,
            )
        )

    def _record_benchmark_result_sync(
        self,
        telemetry_id: int,
        success: bool,
        note: str,
        caller: str | None,
    ) -> bool:
        with self._connect() as connection:
            if caller:
                row = connection.execute(
                    "SELECT metadata FROM telemetry WHERE id=? AND caller=?",
                    (telemetry_id, caller),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT metadata FROM telemetry WHERE id=?", (telemetry_id,)
                ).fetchone()
            if row is None:
                return False
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["benchmark_feedback"] = {
                "success": bool(success),
                "note": redact_secrets(note).strip()[:1000],
                "recorded_at": utc_now(),
            }
            encoded = json.dumps(
                redact_json_value(metadata), separators=(",", ":"), sort_keys=True
            )
            if caller:
                connection.execute(
                    "UPDATE telemetry SET success=?, verified=1, metadata=? "
                    "WHERE id=? AND caller=?",
                    (int(bool(success)), encoded, telemetry_id, caller),
                )
            else:
                connection.execute(
                    "UPDATE telemetry SET success=?, verified=1, metadata=? WHERE id=?",
                    (int(bool(success)), encoded, telemetry_id),
                )
            connection.commit()
        return True

    async def record_benchmark_result(
        self,
        telemetry_id: int,
        success: bool,
        note: str = "",
        *,
        caller: str | None = None,
    ) -> bool:
        return bool(
            await self._write(
                self._record_benchmark_result_sync,
                int(telemetry_id),
                bool(success),
                note,
                str(caller or "")[:160] or None,
            )
        )

    def _telemetry_summary_sync(
        self, limit: int, caller: str | None
    ) -> dict[str, Any]:
        with self._connect() as connection:
            if caller:
                rows = connection.execute(
                    "SELECT * FROM telemetry WHERE caller=? "
                    "ORDER BY id DESC LIMIT ?",
                    (caller, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM telemetry ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        items = [dict(row) for row in rows]
        latencies = sorted(int(item["latency_ms"] or 0) for item in items)

        def percentile(fraction: float) -> int:
            if not latencies:
                return 0
            index = min(len(latencies) - 1, round((len(latencies) - 1) * fraction))
            return latencies[index]

        def aggregate(field: str) -> list[dict[str, Any]]:
            grouped: dict[str, dict[str, Any]] = {}
            for item in items:
                key = str(item.get(field) or "unknown")
                bucket = grouped.setdefault(
                    key,
                    {"name": key, "calls": 0, "verified": 0, "successes": 0, "cost_usd": 0.0},
                )
                bucket["calls"] += 1
                bucket["cost_usd"] += float(item.get("cost_usd") or 0.0)
                if item.get("verified"):
                    bucket["verified"] += 1
                    bucket["successes"] += int(bool(item.get("success")))
            result = []
            for bucket in grouped.values():
                verified = int(bucket["verified"])
                bucket["success_rate"] = (
                    round(int(bucket["successes"]) / verified, 4) if verified else None
                )
                bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)
                result.append(bucket)
            return sorted(result, key=lambda value: int(value["calls"]), reverse=True)

        verified = [item for item in items if item.get("verified")]
        return {
            "sample_size": len(items),
            "verified_samples": len(verified),
            "verified_success_rate": (
                round(sum(int(bool(item.get("success"))) for item in verified) / len(verified), 4)
                if verified
                else None
            ),
            "latency_ms": {
                "average": round(sum(latencies) / len(latencies)) if latencies else 0,
                "p50": percentile(0.50),
                "p95": percentile(0.95),
            },
            "cost_usd": round(sum(float(item.get("cost_usd") or 0.0) for item in items), 6),
            "callers": aggregate("caller"),
            "models": aggregate("model"),
            "routes": aggregate("route"),
            "planes": aggregate("resolved_plane"),
            "kinds": aggregate("request_kind"),
            "fallbacks": aggregate("fallback_reason"),
            "recent": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "created_at",
                        "caller",
                        "request_kind",
                        "route",
                        "resolved_plane",
                        "model",
                        "success",
                        "verified",
                        "latency_ms",
                        "cost_usd",
                        "fallback_reason",
                        "stop_reason",
                    )
                }
                for item in items[:25]
            ],
        }

    async def telemetry_summary(
        self, limit: int = 1000, *, caller: str | None = None
    ) -> dict[str, Any]:
        bounded = max(1, min(int(limit or 1000), 10_000))
        return dict(
            await self._read(
                self._telemetry_summary_sync,
                bounded,
                str(caller or "")[:160] or None,
            )
        )

    def _create_autonomy_job_sync(
        self,
        job_id: str,
        acceptance_hash: str,
        acceptance_text: str,
        continue_token: str,
        request_json: str,
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO autonomy_jobs(
                    job_id, created_at, updated_at, status, acceptance_hash,
                    acceptance_text, continue_token, ledger_cursor, request_json
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, 0, ?)
                """,
                (
                    job_id,
                    now,
                    now,
                    acceptance_hash,
                    redact_secrets(acceptance_text).strip()[:20_000],
                    continue_token,
                    request_json,
                ),
            )
            connection.execute(
                """
                INSERT INTO autonomy_ledger(job_id, event_type, payload, created_at)
                VALUES (?, 'JobCreated', ?, ?)
                """,
                (
                    job_id,
                    json.dumps(
                        {"acceptance_hash": acceptance_hash},
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
            self._prune_retention_connection(connection)
            connection.commit()

    async def create_autonomy_job(
        self,
        job_id: str,
        *,
        acceptance_hash: str,
        acceptance_text: str,
        continue_token: str,
        request: dict[str, Any] | None = None,
    ) -> None:
        encoded = json.dumps(
            redact_json_value(request or {}), separators=(",", ":"), default=str
        )
        await self._write(
            self._create_autonomy_job_sync,
            job_id,
            acceptance_hash,
            acceptance_text,
            continue_token,
            encoded,
        )

    def _load_autonomy_job_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM autonomy_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    async def load_autonomy_job(self, job_id: str) -> dict[str, Any] | None:
        return await self._read(self._load_autonomy_job_sync, job_id)

    def _load_autonomy_by_token_sync(self, continue_token: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM autonomy_jobs WHERE continue_token=?",
                (continue_token,),
            ).fetchone()
        return dict(row) if row is not None else None

    async def load_autonomy_by_token(self, continue_token: str) -> dict[str, Any] | None:
        token = str(continue_token or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("continue_token must be a 32-character hex token")
        return await self._read(self._load_autonomy_by_token_sync, token)

    def _set_autonomy_status_sync(self, job_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE autonomy_jobs SET status=?, updated_at=? WHERE job_id=?",
                (status, utc_now(), job_id),
            )
            connection.commit()

    async def set_autonomy_status(self, job_id: str, status: str) -> None:
        await self._write(self._set_autonomy_status_sync, job_id, status)

    def _claim_autonomy_sync(
        self, job_id: str, claim_lease: str, ttl_seconds: int
    ) -> bool:
        now = utc_now()
        expires = datetime.now(UTC).timestamp() + max(5, int(ttl_seconds))
        expires_at = datetime.fromtimestamp(expires, tz=UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT claim_lease, claim_expires_at FROM autonomy_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            current = row["claim_lease"]
            current_exp = row["claim_expires_at"]
            if current and current_exp and current_exp > now and current != claim_lease:
                return False
            connection.execute(
                """
                UPDATE autonomy_jobs
                SET claim_lease=?, claim_expires_at=?, updated_at=?
                WHERE job_id=?
                """,
                (claim_lease, expires_at, now, job_id),
            )
            connection.commit()
        return True

    async def claim_autonomy(
        self, job_id: str, claim_lease: str, *, ttl_seconds: int = 120
    ) -> bool:
        return bool(
            await self._write(
                self._claim_autonomy_sync, job_id, claim_lease, int(ttl_seconds)
            )
        )

    def _release_autonomy_claim_sync(self, job_id: str, claim_lease: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE autonomy_jobs
                SET claim_lease=NULL, claim_expires_at=NULL, updated_at=?
                WHERE job_id=? AND claim_lease=?
                """,
                (utc_now(), job_id, claim_lease),
            )
            connection.commit()

    async def release_autonomy_claim(self, job_id: str, claim_lease: str) -> None:
        await self._write(self._release_autonomy_claim_sync, job_id, claim_lease)

    def _merge_agent_job_enrichment_sync(
        self, job_id: str, enrichment: dict[str, Any]
    ) -> str | None:
        """Attach enrichment without downgrading a terminal durable status."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, payload FROM agent_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            payload: dict[str, Any] = {}
            if row["payload"]:
                try:
                    decoded = json.loads(row["payload"])
                    if isinstance(decoded, dict):
                        payload = decoded
                except json.JSONDecodeError:
                    payload = {}
            safe_enrichment = redact_json_value(enrichment)
            if status in {"complete", "error", "needs_continuation"}:
                payload.update(safe_enrichment)
                if enrichment.get("review_kind") and payload.get("status") == "complete":
                    payload["review"] = payload.get("text")
            else:
                pending = payload.get("pending_enrichment")
                if not isinstance(pending, dict):
                    pending = {}
                pending.update(safe_enrichment)
                payload["pending_enrichment"] = pending
            connection.execute(
                "UPDATE agent_jobs SET payload=? WHERE job_id=?",
                (
                    json.dumps(
                        redact_json_value(payload),
                        separators=(",", ":"),
                        default=str,
                    ),
                    job_id,
                ),
            )
            connection.commit()
        return status

    async def merge_agent_job_enrichment(
        self, job_id: str, enrichment: dict[str, Any]
    ) -> str | None:
        return await self._write(
            self._merge_agent_job_enrichment_sync, job_id, dict(enrichment)
        )

    def _append_autonomy_event_sync(
        self, job_id: str, event_type: str, payload: dict[str, Any]
    ) -> int:
        now = utc_now()
        encoded = json.dumps(
            redact_json_value(payload), separators=(",", ":"), default=str
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO autonomy_ledger(job_id, event_type, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, event_type, encoded, now),
            )
            event_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE autonomy_jobs
                SET ledger_cursor=?, updated_at=?
                WHERE job_id=?
                """,
                (event_id, now, job_id),
            )
            connection.commit()
        return event_id

    async def append_autonomy_event(
        self, job_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> int:
        return int(
            await self._write(
                self._append_autonomy_event_sync,
                job_id,
                event_type,
                dict(payload or {}),
            )
        )

    def _list_autonomy_events_sync(
        self, job_id: str, after_id: int, limit: int
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, event_type, payload, created_at
                FROM autonomy_ledger
                WHERE job_id=? AND id>?
                ORDER BY id ASC
                LIMIT ?
                """,
                (job_id, int(after_id), bounded),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item["payload"])
            except json.JSONDecodeError:
                item["payload"] = {"raw": item["payload"]}
            events.append(item)
        return events

    async def list_autonomy_events(
        self, job_id: str, *, after_id: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        return list(
            await self._read(
                self._list_autonomy_events_sync,
                job_id,
                int(after_id),
                int(limit),
            )
        )

    def _put_autonomy_artifact_sync(
        self, job_id: str, digest: str, kind: str, content: str
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO autonomy_artifacts(hash, job_id, kind, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(hash) DO NOTHING
                """,
                (
                    digest,
                    job_id,
                    kind,
                    redact_secrets(content).strip()[:100_000],
                    now,
                ),
            )
            self._prune_retention_connection(connection)
            connection.commit()

    async def put_autonomy_artifact(
        self, job_id: str, digest: str, *, kind: str, content: str
    ) -> None:
        await self._write(
            self._put_autonomy_artifact_sync,
            job_id,
            digest,
            kind,
            content,
        )

    def _get_autonomy_artifact_sync(self, digest: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT hash, job_id, kind, content, created_at "
                "FROM autonomy_artifacts WHERE hash=?",
                (digest,),
            ).fetchone()
        return dict(row) if row is not None else None

    async def get_autonomy_artifact(self, digest: str) -> dict[str, Any] | None:
        return await self._read(self._get_autonomy_artifact_sync, digest)

    # --- Mission controller (UNIGROK_MISSION_V2) ---------------------------------

    def _create_mission_sync(
        self,
        mission_id: str,
        job_id: str,
        acceptance_hash: str,
        acceptance_text: str,
        continue_token: str,
        package_json: str,
        lease_token: str,
        lease_generation: int,
        lease_expires_at: str,
    ) -> None:
        now = utc_now()
        checkpoint = {
            "epoch": 0,
            "plan": [],
            "step_cursor": 0,
            "previous_checkpoint_hash": None,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO missions(
                    mission_id, job_id, created_at, updated_at, status,
                    acceptance_hash, acceptance_text, continue_token,
                    package_json, checkpoint_json, checkpoint_version,
                    lease_token, lease_generation, lease_expires_at,
                    envelope_version, verify_failures, ledger_cursor
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, 0, ?, ?, ?, 1, 0, 0)
                """,
                (
                    mission_id,
                    job_id,
                    now,
                    now,
                    acceptance_hash,
                    redact_secrets(acceptance_text),
                    continue_token,
                    package_json,
                    json.dumps(checkpoint, separators=(",", ":"), default=str),
                    lease_token,
                    int(lease_generation),
                    lease_expires_at,
                ),
            )
            self._prune_retention_connection(connection)
            connection.commit()

    async def create_mission(
        self,
        mission_id: str,
        *,
        job_id: str,
        acceptance_hash: str,
        acceptance_text: str,
        continue_token: str,
        package: dict[str, Any],
        lease_token: str,
        lease_generation: int,
        lease_expires_at: str,
    ) -> None:
        await self._write(
            self._create_mission_sync,
            mission_id,
            job_id,
            acceptance_hash,
            acceptance_text,
            continue_token,
            json.dumps(
                redact_json_value(package), separators=(",", ":"), default=str
            ),
            lease_token,
            int(lease_generation),
            lease_expires_at,
        )

    def _load_mission_sync(self, mission_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key, field in (("package", "package_json"), ("checkpoint", "checkpoint_json")):
            try:
                item[key] = json.loads(item.get(field) or "{}")
            except json.JSONDecodeError:
                item[key] = {}
        return item

    async def load_mission(self, mission_id: str) -> dict[str, Any] | None:
        return await self._read(self._load_mission_sync, mission_id)

    def _load_mission_by_job_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT mission_id FROM missions WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return self._load_mission_sync(str(row["mission_id"]))

    async def load_mission_by_job(self, job_id: str) -> dict[str, Any] | None:
        return await self._read(self._load_mission_by_job_sync, job_id)

    def _load_mission_by_token_sync(self, continue_token: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT mission_id FROM missions WHERE continue_token=?",
                (continue_token,),
            ).fetchone()
        if row is None:
            return None
        return self._load_mission_sync(str(row["mission_id"]))

    async def load_mission_by_token(self, continue_token: str) -> dict[str, Any] | None:
        token = str(continue_token or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("continue_token must be a 32-character hex token")
        return await self._read(self._load_mission_by_token_sync, token)

    def _claim_mission_sync(
        self,
        mission_id: str,
        lease_token: str,
        ttl_seconds: int,
        expect_generation: int | None,
    ) -> tuple[bool, int]:
        from unigrok_public.mission.lease import lease_expiry_iso

        now = utc_now()
        expires_at = lease_expiry_iso(ttl_seconds=ttl_seconds)
        expected = int(expect_generation) if expect_generation is not None else None
        with self._connect() as connection:
            claimed = connection.execute(
                """
                UPDATE missions
                SET lease_token=:lease_token,
                    lease_generation=CASE
                        WHEN lease_token=:lease_token
                             AND :expect_generation IS NOT NULL
                             AND lease_generation=:expect_generation
                        THEN lease_generation
                        ELSE lease_generation + 1
                    END,
                    lease_expires_at=:expires_at,
                    status=CASE WHEN status IN (
                        'queued','waiting_event'
                    ) THEN 'running' ELSE status END,
                    updated_at=:now
                WHERE mission_id=:mission_id
                  AND status IN ('queued','waiting_event','running','verifying')
                  AND (
                      (
                          lease_token=:lease_token
                          AND :expect_generation IS NOT NULL
                          AND lease_generation=:expect_generation
                      )
                      OR COALESCE(lease_token, '')=''
                      OR lease_expires_at IS NULL
                      OR lease_expires_at <= :now
                  )
                RETURNING lease_generation
                """,
                {
                    "mission_id": mission_id,
                    "lease_token": lease_token,
                    "expect_generation": expected,
                    "expires_at": expires_at,
                    "now": now,
                },
            ).fetchall()
            if claimed:
                generation = int(claimed[0]["lease_generation"] or 0)
                connection.commit()
                return True, generation

            # Preserve the public return shape: a rejected claim reports the
            # current fence, while a missing mission reports generation zero.
            row = connection.execute(
                "SELECT lease_generation FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            connection.commit()
        return False, int(row["lease_generation"] or 0) if row is not None else 0

    async def claim_mission(
        self,
        mission_id: str,
        *,
        lease_token: str,
        ttl_seconds: int = 180,
        expect_generation: int | None = None,
    ) -> tuple[bool, int]:
        return await self._write(
            self._claim_mission_sync,
            mission_id,
            lease_token,
            int(ttl_seconds),
            expect_generation,
        )

    def _heartbeat_mission_sync(
        self,
        mission_id: str,
        lease_token: str,
        lease_generation: int,
        ttl_seconds: int,
    ) -> bool:
        from unigrok_public.mission.lease import lease_expiry_iso

        now = utc_now()
        expires_at = lease_expiry_iso(ttl_seconds=ttl_seconds)
        with self._connect() as connection:
            cur = connection.execute(
                """
                UPDATE missions
                SET lease_expires_at=?, updated_at=?
                WHERE mission_id=? AND lease_token=? AND lease_generation=?
                """,
                (expires_at, now, mission_id, lease_token, int(lease_generation)),
            )
            connection.commit()
            return cur.rowcount == 1

    async def heartbeat_mission(
        self,
        mission_id: str,
        *,
        lease_token: str,
        lease_generation: int,
        ttl_seconds: int = 180,
    ) -> bool:
        return bool(
            await self._write(
                self._heartbeat_mission_sync,
                mission_id,
                lease_token,
                int(lease_generation),
                int(ttl_seconds),
            )
        )

    def _cas_mission_status_sync(
        self,
        mission_id: str,
        expect_status: str,
        expect_version: int,
        expect_lease_generation: int,
        new_status: str,
        expect_lease_token: str | None,
        clear_lease: bool,
        checkpoint_update: dict[str, Any] | None,
        bump_verify_failure: bool,
    ) -> bool:
        from unigrok_public.mission.types import legal_transition

        if not legal_transition(expect_status, new_status):
            return False
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, checkpoint_version, lease_generation, checkpoint_json, "
                "verify_failures, job_id FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                return False
            if str(row["status"]) != str(expect_status):
                return False
            if int(row["checkpoint_version"] or 0) != int(expect_version):
                return False
            if int(row["lease_generation"] or 0) != int(expect_lease_generation):
                return False
            try:
                checkpoint = json.loads(row["checkpoint_json"] or "{}")
            except json.JSONDecodeError:
                checkpoint = {}
            if checkpoint_update:
                checkpoint.update(redact_json_value(checkpoint_update))
            new_version = int(expect_version) + 1
            verify_failures = int(row["verify_failures"] or 0)
            if bump_verify_failure:
                verify_failures += 1
            if clear_lease:
                # Bump lease_generation when releasing so a stale worker cannot
                # CommitDone after sweeper requeue + new claim.
                fenced_gen = int(expect_lease_generation) + 1
                cur = connection.execute(
                    """
                    UPDATE missions
                    SET status=?, checkpoint_json=?, checkpoint_version=?,
                        lease_token=NULL, lease_expires_at=NULL,
                        lease_generation=?,
                        verify_failures=?, updated_at=?
                    WHERE mission_id=? AND status=? AND checkpoint_version=?
                          AND lease_generation=?
                          AND (? IS NULL OR lease_token=?)
                    """,
                    (
                        new_status,
                        json.dumps(checkpoint, separators=(",", ":"), default=str),
                        new_version,
                        fenced_gen,
                        verify_failures,
                        now,
                        mission_id,
                        expect_status,
                        int(expect_version),
                        int(expect_lease_generation),
                        expect_lease_token,
                        expect_lease_token,
                    ),
                )
            else:
                cur = connection.execute(
                    """
                    UPDATE missions
                    SET status=?, checkpoint_json=?, checkpoint_version=?,
                        verify_failures=?, updated_at=?
                    WHERE mission_id=? AND status=? AND checkpoint_version=?
                          AND lease_generation=?
                          AND (? IS NULL OR lease_token=?)
                    """,
                    (
                        new_status,
                        json.dumps(checkpoint, separators=(",", ":"), default=str),
                        new_version,
                        verify_failures,
                        now,
                        mission_id,
                        expect_status,
                        int(expect_version),
                        int(expect_lease_generation),
                        expect_lease_token,
                        expect_lease_token,
                    ),
                )
            changed = int(cur.rowcount) == 1
            if changed and new_status in {
                "complete",
                "failed",
                "budget_exhausted",
                "cancelled",
            }:
                connection.execute(
                    "UPDATE autonomy_jobs SET status=?, updated_at=? WHERE job_id=?",
                    (
                        "committed" if new_status == "complete" else "terminal",
                        now,
                        str(row["job_id"]),
                    ),
                )
            connection.commit()
            return changed

    async def cas_mission_status(
        self,
        mission_id: str,
        *,
        expect_status: str,
        expect_version: int,
        expect_lease_generation: int,
        new_status: str,
        expect_lease_token: str | None = None,
        clear_lease: bool = False,
        checkpoint_update: dict[str, Any] | None = None,
        bump_verify_failure: bool = False,
    ) -> bool:
        return bool(
            await self._write(
                self._cas_mission_status_sync,
                mission_id,
                expect_status,
                int(expect_version),
                int(expect_lease_generation),
                new_status,
                expect_lease_token,
                clear_lease,
                checkpoint_update,
                bump_verify_failure,
            )
        )

    def _touch_mission_envelope_sync(
        self,
        mission_id: str,
        envelope_version: int,
        lease_token: str,
        lease_generation: int,
    ) -> bool:
        """Record deployment envelope version; never raise mission caps here."""
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT envelope_version, package_json FROM missions "
                "WHERE mission_id=? AND lease_token=? AND lease_generation=? "
                "AND status NOT IN ('complete','failed','budget_exhausted','cancelled')",
                (mission_id, lease_token, int(lease_generation)),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            current = int(row["envelope_version"] or 1)
            new_v = max(current, int(envelope_version))
            try:
                package = json.loads(row["package_json"] or "{}")
            except json.JSONDecodeError:
                package = {}
            package["bound_envelope_version"] = new_v
            updated = connection.execute(
                """
                UPDATE missions
                SET envelope_version=?, package_json=?, updated_at=?
                WHERE mission_id=? AND lease_token=? AND lease_generation=?
                  AND status NOT IN ('complete','failed','budget_exhausted','cancelled')
                """,
                (
                    new_v,
                    json.dumps(
                        redact_json_value(package), separators=(",", ":"), default=str
                    ),
                    now,
                    mission_id,
                    lease_token,
                    int(lease_generation),
                ),
            )
            connection.commit()
            return int(updated.rowcount) == 1

    async def touch_mission_envelope(
        self,
        mission_id: str,
        *,
        envelope_version: int,
        lease_token: str,
        lease_generation: int,
    ) -> bool:
        return bool(
            await self._write(
                self._touch_mission_envelope_sync,
                mission_id,
                int(envelope_version),
                lease_token,
                int(lease_generation),
            )
        )

    def _append_mission_event_sync(
        self,
        mission_id: str,
        event_type: str,
        payload: dict[str, Any],
        lease_token: str | None,
        lease_generation: int | None,
    ) -> int:
        now = utc_now()
        encoded = json.dumps(
            redact_json_value(payload), separators=(",", ":"), default=str
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if lease_token is not None or lease_generation is not None:
                if lease_token is None or lease_generation is None:
                    connection.rollback()
                    return 0
                owner = connection.execute(
                    "SELECT 1 FROM missions WHERE mission_id=? AND lease_token=? "
                    "AND lease_generation=?",
                    (mission_id, lease_token, int(lease_generation)),
                ).fetchone()
                if owner is None:
                    connection.rollback()
                    return 0
            cursor = connection.execute(
                """
                INSERT INTO mission_ledger(mission_id, event_type, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (mission_id, event_type, encoded, now),
            )
            event_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE missions SET ledger_cursor=?, updated_at=? WHERE mission_id=?
                """,
                (event_id, now, mission_id),
            )
            connection.commit()
        return event_id

    async def append_mission_event(
        self,
        mission_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        lease_token: str | None = None,
        lease_generation: int | None = None,
    ) -> int:
        return int(
            await self._write(
                self._append_mission_event_sync,
                mission_id,
                event_type,
                dict(payload or {}),
                lease_token,
                lease_generation,
            )
        )

    def _put_mission_artifact_sync(
        self,
        mission_id: str,
        digest: str,
        kind: str,
        sealed: str,
        projection: str,
        lease_token: str | None,
        lease_generation: int | None,
    ) -> bool:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if lease_token is not None or lease_generation is not None:
                if lease_token is None or lease_generation is None:
                    connection.rollback()
                    return False
                owner = connection.execute(
                    "SELECT 1 FROM missions WHERE mission_id=? AND lease_token=? "
                    "AND lease_generation=?",
                    (mission_id, lease_token, int(lease_generation)),
                ).fetchone()
                if owner is None:
                    connection.rollback()
                    return False
            connection.execute(
                """
                INSERT INTO mission_artifacts(
                    hash, mission_id, kind, sealed, projection, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash) DO NOTHING
                """,
                (
                    digest,
                    mission_id,
                    kind,
                    redact_secrets(sealed),
                    redact_secrets(projection),
                    now,
                ),
            )
            connection.commit()
        return True

    async def put_mission_artifact(
        self,
        mission_id: str,
        digest: str,
        *,
        kind: str,
        sealed: str,
        projection: str,
        lease_token: str | None = None,
        lease_generation: int | None = None,
    ) -> bool:
        return bool(
            await self._write(
                self._put_mission_artifact_sync,
                mission_id,
                digest,
                kind,
                sealed,
                projection,
                lease_token,
                lease_generation,
            )
        )

    def _get_mission_artifact_sync(self, digest: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT hash, mission_id, kind, sealed, projection, created_at "
                "FROM mission_artifacts WHERE hash=?",
                (digest,),
            ).fetchone()
        return dict(row) if row is not None else None

    async def get_mission_artifact(self, digest: str) -> dict[str, Any] | None:
        return await self._read(self._get_mission_artifact_sync, digest)

    def _append_mission_evidence_sync(
        self,
        mission_id: str,
        klass: str,
        digest: str,
        payload: dict[str, Any],
        artifact_refs: list[str],
        lease_generation: int,
        lease_token: str | None,
    ) -> bool:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_generation, lease_token FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            if int(row["lease_generation"] or 0) != int(lease_generation):
                connection.rollback()
                return False
            if lease_token is not None and str(row["lease_token"] or "") != lease_token:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO mission_evidence(
                    mission_id, class, digest, payload, artifact_refs,
                    lease_generation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id, digest) DO NOTHING
                """,
                (
                    mission_id,
                    klass,
                    digest,
                    json.dumps(
                        redact_json_value(payload),
                        separators=(",", ":"),
                        default=str,
                    ),
                    json.dumps(list(artifact_refs), separators=(",", ":")),
                    int(lease_generation),
                    now,
                ),
            )
            connection.commit()
        return True

    async def append_mission_evidence(
        self,
        mission_id: str,
        *,
        klass: str,
        digest: str,
        payload: dict[str, Any],
        artifact_refs: list[str],
        lease_generation: int,
        lease_token: str | None = None,
    ) -> bool:
        return bool(
            await self._write(
                self._append_mission_evidence_sync,
                mission_id,
                klass,
                digest,
                payload,
                artifact_refs,
                int(lease_generation),
                lease_token,
            )
        )

    def _list_mission_evidence_sync(self, mission_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT class, digest, payload, artifact_refs, lease_generation, created_at
                FROM mission_evidence WHERE mission_id=? ORDER BY id ASC
                """,
                (mission_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item["payload"])
            except json.JSONDecodeError:
                item["payload"] = {}
            try:
                item["artifact_refs"] = json.loads(item["artifact_refs"])
            except json.JSONDecodeError:
                item["artifact_refs"] = []
            out.append(item)
        return out

    async def list_mission_evidence(self, mission_id: str) -> list[dict[str, Any]]:
        return list(await self._read(self._list_mission_evidence_sync, mission_id))

    def _put_side_effect_sync(
        self,
        quantum_key: str,
        mission_id: str,
        receipt: dict[str, Any],
        lease_generation: int,
    ) -> bool:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_generation FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                return False
            if int(row["lease_generation"] or 0) != int(lease_generation):
                return False
            existing = connection.execute(
                "SELECT quantum_key FROM mission_side_effects WHERE quantum_key=?",
                (quantum_key,),
            ).fetchone()
            if existing is not None:
                return True  # idempotent hit
            connection.execute(
                """
                INSERT INTO mission_side_effects(
                    quantum_key, mission_id, receipt_json, lease_generation, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    quantum_key,
                    mission_id,
                    json.dumps(
                        redact_json_value(receipt),
                        separators=(",", ":"),
                        default=str,
                    ),
                    int(lease_generation),
                    now,
                ),
            )
            connection.commit()
        return True

    async def put_mission_side_effect(
        self,
        quantum_key: str,
        *,
        mission_id: str,
        receipt: dict[str, Any],
        lease_generation: int,
    ) -> bool:
        return bool(
            await self._write(
                self._put_side_effect_sync,
                quantum_key,
                mission_id,
                receipt,
                int(lease_generation),
            )
        )

    def _list_expired_mission_leases_sync(self, limit: int) -> list[dict[str, Any]]:
        now = utc_now()
        bounded = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT mission_id, status, checkpoint_version, lease_generation,
                       lease_expires_at
                FROM missions
                WHERE lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                  AND status NOT IN ('complete','failed','cancelled','budget_exhausted')
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (now, bounded),
            ).fetchall()
        return [dict(row) for row in rows]

    async def list_expired_mission_leases(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(await self._read(self._list_expired_mission_leases_sync, int(limit)))

    def _requeue_expired_mission_sync(
        self,
        mission_id: str,
        expect_status: str,
        expect_version: int,
        expect_lease_generation: int,
        expect_lease_expires_at: str,
    ) -> bool:
        from unigrok_public.mission.types import MissionStatus, legal_transition

        if not legal_transition(expect_status, MissionStatus.QUEUED):
            return False
        now = utc_now()
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE missions
                SET status='queued', checkpoint_version=checkpoint_version + 1,
                    lease_token=NULL, lease_expires_at=NULL,
                    lease_generation=lease_generation + 1, updated_at=?
                WHERE mission_id=? AND status=? AND checkpoint_version=?
                  AND lease_generation=? AND lease_expires_at=?
                  AND lease_expires_at < ?
                """,
                (
                    now,
                    mission_id,
                    expect_status,
                    int(expect_version),
                    int(expect_lease_generation),
                    expect_lease_expires_at,
                    now,
                ),
            )
            connection.commit()
            return int(changed.rowcount) == 1

    async def requeue_expired_mission(
        self,
        mission_id: str,
        *,
        expect_status: str,
        expect_version: int,
        expect_lease_generation: int,
        expect_lease_expires_at: str,
    ) -> bool:
        return bool(
            await self._write(
                self._requeue_expired_mission_sync,
                mission_id,
                expect_status,
                int(expect_version),
                int(expect_lease_generation),
                expect_lease_expires_at,
            )
        )
