# tests/test_knowledge.py
# Local-first knowledge memory: v7 migration + FTS5 probe/fallback ranking,
# distillation (FactList via the shared tool-free structured-parse machinery,
# distill jobs, auto-distill gating), the reworked get_dynamic_context
# (ranked candidate files + bounded knowledge injection), the knowledge MCP
# tools/resource, and the capability-gated xAI collections adapter.

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

import src.utils as utils_module
from evals.fakes import FakeClient
from src.jobs import JobManager
from src.utils import (
    DEFAULT_CODING_MODEL,
    FactList,
    GrokSessionStore,
    PathResolver,
    _active_xai_breaker_scope,
    _parse_structured,
    append_and_save_history,
    format_knowledge_notes,
    git_cache,
    record_xai_failure,
    search_knowledge_collection,
    sync_fact_to_collection,
)
from src.tools.knowledge import (
    distill_session,
    forget_fact,
    remember_fact,
    search_knowledge,
)


@pytest.fixture
async def kstore(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "knowledge.db")
    yield s
    await s.close()


async def _force_fallback_store(tmp_path, monkeypatch, name="fallback.db"):
    """A store whose FTS5 probe is forced to fail (the no-FTS5 build path)."""
    async def _no_fts(conn):
        return False

    monkeypatch.setattr(GrokSessionStore, "_probe_fts5", staticmethod(_no_fts))
    s = GrokSessionStore(db_path=tmp_path / name)
    await s._ensure_initialized()
    return s


# ─────────────────────────────────────────────────────────────────────────────
# v7 migration + knowledge store methods (FTS5 present path)
# ─────────────────────────────────────────────────────────────────────────────

class TestKnowledgeMigration:
    @pytest.mark.asyncio
    async def test_v7_migration_creates_knowledge_table(self, kstore):
        await kstore._ensure_initialized()
        async with kstore._conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row[0] >= 7
        async with kstore._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge';"
        ) as cursor:
            assert await cursor.fetchone() is not None
        async with kstore._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index';"
        ) as cursor:
            indexes = {r[0] for r in await cursor.fetchall()}
            assert {"idx_knowledge_scope", "idx_knowledge_created_at"} <= indexes
        # This build has FTS5 (verified in CI images too): the index table
        # exists and the store flags it available.
        assert kstore._knowledge_fts is True
        async with kstore._conn.execute(
            "SELECT name FROM sqlite_master WHERE name='knowledge_fts';"
        ) as cursor:
            assert await cursor.fetchone() is not None


class TestKnowledgeStoreFTS:
    @pytest.mark.asyncio
    async def test_search_ranked_by_bm25_with_uniform_score(self, kstore):
        await kstore.save_fact("The HTTP gateway streams SSE progress events")
        await kstore.save_fact("SQLite store uses WAL journaling with a read pool")
        await kstore.save_fact("Session compaction summarizes the oldest half")

        results = await kstore.search_facts("why does the sqlite wal read pool lock?")
        assert results, "FTS search must find the matching fact"
        assert results[0]["fact"].startswith("SQLite store uses WAL")
        # score is exposed higher-is-better on BOTH ranking paths.
        assert all("score" in item for item in results)
        assert results == sorted(results, key=lambda i: i["score"], reverse=True)

    @pytest.mark.asyncio
    async def test_save_fact_is_deduped_bounded_and_redacted(self, kstore):
        fid = await kstore.save_fact("Deploy key is XAI_API_KEY=xai-verysecret1234 for CI")
        dup = await kstore.save_fact("Deploy key is XAI_API_KEY=xai-verysecret1234 for CI")
        assert dup == fid, "identical (scope, fact) must touch, not duplicate"
        assert await kstore.count_facts() == 1
        rows = await kstore.list_facts()
        assert "xai-verysecret1234" not in rows[0]["fact"]
        assert "[REDACTED" in rows[0]["fact"]
        assert rows[0]["uses"] == 1  # the dedup touch
        # Empty facts are rejected.
        assert await kstore.save_fact("   ") is None

    @pytest.mark.asyncio
    async def test_save_fact_scope_is_bounded_and_redacted(self, kstore):
        """scope is client-controlled via remember_fact: it must land bounded
        and redacted at rest like the sibling fact/source columns, and the
        SAME normalization must apply on lookup so scoped reads keep
        matching."""
        huge_scope = "sess-" + "x" * 5000
        fid = await kstore.save_fact("bounded scope fact", scope=huge_scope)
        rows = await kstore.list_facts(scope=huge_scope)  # symmetric lookup
        assert [r["id"] for r in rows] == [fid]
        assert len(rows[0]["scope"]) <= 200

        secret_scope = "team XAI_API_KEY=xai-scopedsecret9876"
        await kstore.save_fact("redacted scope fact", scope=secret_scope)
        stored = await kstore.list_facts(scope=secret_scope)
        assert stored and "xai-scopedsecret9876" not in stored[0]["scope"]
        assert "[REDACTED" in stored[0]["scope"]

        # Blank scope still degrades to 'global'.
        gid = await kstore.save_fact("global scope fact", scope="   ")
        globals_ = {r["id"] for r in await kstore.list_facts(scope="global")}
        assert gid in globals_

    @pytest.mark.asyncio
    async def test_scoped_search_includes_global_facts(self, kstore):
        await kstore.save_fact("The staging cluster runs kubernetes ingress", scope="global")
        await kstore.save_fact("kubernetes namespace quota is 4Gi here", scope="sess-a")
        await kstore.save_fact("kubernetes secret rotation happens weekly", scope="sess-b")

        scoped = await kstore.search_facts("kubernetes ingress quota", scope="sess-a")
        scopes = {item["scope"] for item in scoped}
        assert "sess-a" in scopes and "global" in scopes
        assert "sess-b" not in scopes
        # scope=None searches everything.
        everything = await kstore.search_facts("kubernetes", scope=None, limit=10)
        assert {item["scope"] for item in everything} == {"global", "sess-a", "sess-b"}

    @pytest.mark.asyncio
    async def test_touch_delete_count_list_roundtrip(self, kstore):
        fid1 = await kstore.save_fact("alpha fact about routing")
        fid2 = await kstore.save_fact("beta fact about storage")
        # Duplicate ids in one call collapse (SQL IN semantics); separate
        # touches accumulate.
        await kstore.touch_facts([fid1, fid1])
        await kstore.touch_facts([fid1])
        rows = {r["id"]: r for r in await kstore.list_facts()}
        assert rows[fid1]["uses"] == 2
        assert rows[fid1]["last_used_at"] >= rows[fid1]["created_at"]
        assert rows[fid2]["uses"] == 0
        # touch with junk ids is a no-op, never an error.
        await kstore.touch_facts(["not-a-number", None])

        assert await kstore.delete_fact(fid1) is True
        assert await kstore.delete_fact(fid1) is False
        assert await kstore.count_facts() == 1
        # The FTS index row went with it: no stale match remains.
        assert await kstore.search_facts("alpha routing") == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_nothing(self, kstore):
        await kstore.save_fact("some stored fact")
        assert await kstore.search_facts("") == []
        assert await kstore.search_facts("the and for") == []  # all stopwords


# ─────────────────────────────────────────────────────────────────────────────
# FTS5-absent path: forced fallback ranking + cross-build reindex
# ─────────────────────────────────────────────────────────────────────────────

class TestKnowledgeStoreFallback:
    @pytest.mark.asyncio
    async def test_fallback_ranks_by_term_overlap(self, tmp_path, monkeypatch):
        s = await _force_fallback_store(tmp_path, monkeypatch)
        try:
            assert s._knowledge_fts is False
            async with s._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='knowledge_fts';"
            ) as cursor:
                assert await cursor.fetchone() is None, "no index table without FTS5"

            await s.save_fact("The HTTP gateway streams SSE progress events")
            await s.save_fact("SQLite store uses WAL journaling with a read pool")
            results = await s.search_facts("sqlite wal journaling issue")
            assert len(results) == 1
            assert results[0]["fact"].startswith("SQLite store")
            assert results[0]["score"] == pytest.approx(3 / 4)  # 3 of 4 query terms
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_fallback_respects_scope_and_limit(self, tmp_path, monkeypatch):
        s = await _force_fallback_store(tmp_path, monkeypatch)
        try:
            await s.save_fact("docker compose file lives at ops/docker-compose.yml", scope="global")
            await s.save_fact("docker builds run on the ci runner", scope="sess-x")
            scoped = await s.search_facts("docker compose", scope="sess-x", limit=1)
            assert len(scoped) == 1
            # Overlap ranking puts the 2-term match first even under a limit.
            assert scoped[0]["scope"] == "global"
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_reopen_with_fts_reindexes_fallback_writes(self, tmp_path, monkeypatch):
        """Facts written while FTS5 was unavailable become MATCH-able after
        the db is reopened on a build with FTS5 (id-divergence rebuild)."""
        s = await _force_fallback_store(tmp_path, monkeypatch, name="mixed.db")
        await s.save_fact("terraform state lives in the gcs bucket")
        await s.close()
        monkeypatch.undo()  # restore the real probe

        s2 = GrokSessionStore(db_path=tmp_path / "mixed.db")
        try:
            await s2._ensure_initialized()
            assert s2._knowledge_fts is True
            results = await s2.search_facts("where is the terraform state bucket")
            assert len(results) == 1
            assert "terraform" in results[0]["fact"]
        finally:
            await s2.close()

    @pytest.mark.asyncio
    async def test_reinit_repairs_diverged_index_with_equal_counts(self, tmp_path):
        """Regression (round-3 review): an unindexed insert plus an unindexed
        delete cancel out in row COUNTS while both rows stay wrong — the
        repair check must compare ids, not totals, or the new fact is
        invisible to FTS search forever."""
        from src.utils import _task_terms

        db_path = tmp_path / "diverge.db"
        s = GrokSessionStore(db_path=db_path)
        await s.save_fact("alpha fact about parser tokens")
        id_beta = await s.save_fact("beta fact about cache eviction")
        # Simulate dual-writes made after a transient FTS failure flipped
        # _knowledge_fts off: gamma lands in knowledge with NO index entry,
        # beta's row is deleted but its index entry survives. Counts stay
        # equal (2 == 2) while the index disagrees on both ids.
        gamma = "gamma fact about distill jobs"
        async with s._lock:
            await s._conn.execute(
                "INSERT INTO knowledge (scope, fact, source, terms, created_at, last_used_at, uses) "
                "VALUES ('global', ?, '', ?, '2026-01-01', '2026-01-01', 0)",
                (gamma, " ".join(_task_terms(gamma))),
            )
            await s._conn.execute("DELETE FROM knowledge WHERE id = ?", (id_beta,))
            await s._conn.commit()
        await s.close()

        s2 = GrokSessionStore(db_path=db_path)
        try:
            results = await s2.search_facts("gamma distill jobs")
            assert any("gamma" in str(r["fact"]) for r in results), (
                "diverged index was not rebuilt: unindexed fact stays unfindable"
            )
            # The orphaned beta index entry is gone too.
            assert await s2.search_facts("beta cache eviction") == []
        finally:
            await s2.close()


# ─────────────────────────────────────────────────────────────────────────────
# Distillation: FactList schema + shared structured-parse machinery
# ─────────────────────────────────────────────────────────────────────────────

class TestFactListParse:
    def test_factlist_enforces_3_to_8_items(self):
        FactList(facts=["a", "b", "c"])
        FactList(facts=[str(i) for i in range(8)])
        with pytest.raises(ValidationError):
            FactList(facts=["only", "two"])
        with pytest.raises(ValidationError):
            FactList(facts=[str(i) for i in range(9)])

    @pytest.mark.asyncio
    async def test_parse_structured_is_tool_free_and_shape_generic(self):
        """The extracted helper drives chat.parse(shape) on a dedicated
        TOOL-FREE chat — the same machinery reflection uses, now shared with
        the distiller (no duplication)."""
        client = FakeClient(verdicts=[{
            "facts": ["fact one detail", "fact two detail", "fact three detail"],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            "cost_usd": 0.002,
        }])
        with patch("src.utils.get_xai_client", return_value=client):
            parsed, tokens, cost = await _parse_structured(
                FactList, "sys", "transcript", "grok-build-0.1", timeout=5.0
            )
        assert isinstance(parsed, FactList)
        assert len(parsed.facts) == 3
        assert tokens == 10
        assert cost == pytest.approx(0.002)
        # No tools kwarg may ever reach chat.create.
        assert client.create_calls == [{"model": "grok-build-0.1"}]

    @pytest.mark.asyncio
    async def test_parse_structured_degrades_to_none(self):
        # Missing parse capability.
        chat = MagicMock(spec=["append"])
        client = MagicMock()
        client.chat.create.return_value = chat
        with patch("src.utils.get_xai_client", return_value=client):
            parsed, tokens, cost = await _parse_structured(
                FactList, "sys", "text", "grok-build-0.1", timeout=5.0
            )
        assert parsed is None and tokens == 0 and cost == 0.0
        # Parse/validation error.
        bad = MagicMock()
        bad.parse.side_effect = ValueError("model emitted a tool call")
        client.chat.create.return_value = bad
        with patch("src.utils.get_xai_client", return_value=client):
            parsed, _, _ = await _parse_structured(
                FactList, "sys", "text", "grok-build-0.1", timeout=5.0
            )
        assert parsed is None


class TestDistillJob:
    @pytest.mark.asyncio
    async def test_distill_job_lifecycle_saves_redacted_facts(self, kstore):
        await kstore.save_message("sess-d", "user", "deploy notes with XAI_API_KEY=xai-leakedsecret99")
        await kstore.save_message("sess-d", "assistant", "Deployed via Cloud Run.")
        client = FakeClient(verdicts=[{
            "facts": [
                "The gateway deploys to Cloud Run with UNIGROK_RUNTIME=cloudrun.",
                "CI auth uses XAI_API_KEY=xai-leakedsecret99 from the env.",
                "Session history persists in the SQLite store.",
            ],
            "cost_usd": 0.002,
        }])
        manager = JobManager(job_store=kstore)
        with patch("src.utils.get_xai_client", return_value=client):
            submitted = await manager.submit_distill("sess-d")
            assert submitted["status"] == "queued"
            assert submitted["kind"] == "distill"
            assert submitted["model"] == DEFAULT_CODING_MODEL
            await manager.wait(submitted["job_id"])

        view = await manager.get(submitted["job_id"])
        assert view["status"] == "done"
        assert "Distilled 3 facts" in view["result"]
        assert view["cost_usd"] == pytest.approx(0.002)
        rows = await kstore.list_facts()
        assert len(rows) == 3
        assert all(r["scope"] == "global" for r in rows)
        assert all(r["source"] == "session:sess-d" for r in rows)
        # redact_secrets applied to every distilled fact.
        assert all("xai-leakedsecret99" not in r["fact"] for r in rows)
        assert any("[REDACTED" in r["fact"] for r in rows)

    @pytest.mark.asyncio
    async def test_distill_missing_session_errors(self, kstore):
        manager = JobManager(job_store=kstore)
        with patch("src.utils.get_xai_client", return_value=FakeClient()):
            submitted = await manager.submit_distill("no-such-session")
            await manager.wait(submitted["job_id"])
        view = await manager.get(submitted["job_id"])
        assert view["status"] == "error"
        assert "no stored history" in view["error"]

    @pytest.mark.asyncio
    async def test_distill_parse_unavailable_errors_cleanly(self, kstore):
        """An exhausted/failing parse (verdict-less FakeChat raises) marks the
        job error without crashing and saves no facts."""
        await kstore.save_message("sess-p", "user", "hello")
        manager = JobManager(job_store=kstore)
        with patch("src.utils.get_xai_client", return_value=FakeClient()):
            submitted = await manager.submit_distill("sess-p")
            await manager.wait(submitted["job_id"])
        view = await manager.get(submitted["job_id"])
        assert view["status"] == "error"
        assert "Distillation unavailable" in view["error"]
        assert await kstore.count_facts() == 0

    @pytest.mark.asyncio
    async def test_distill_honors_open_circuit_breaker(self, kstore):
        await kstore.save_message("sess-b", "user", "hello")
        threshold = utils_module._breaker_threshold()
        credential_scope = _active_xai_breaker_scope()
        for _ in range(threshold):
            record_xai_failure(
                DEFAULT_CODING_MODEL,
                credential_scope=credential_scope,
            )
        manager = JobManager(job_store=kstore)
        with patch("src.utils.get_xai_client", return_value=FakeClient()):
            submitted = await manager.submit_distill("sess-b")
            await manager.wait(submitted["job_id"])
        view = await manager.get(submitted["job_id"])
        assert view["status"] == "error"
        assert "circuit breaker" in view["error"].lower()

    @pytest.mark.asyncio
    async def test_distill_job_row_records_explicit_caller(self, kstore, monkeypatch):
        """Regression (round-3 review): distill jobs were the one job type
        persisted with caller=NULL — attribution must match submit()."""
        monkeypatch.setattr(JobManager, "_run_distill_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=kstore)

        view = await manager.submit_distill("sess-attr", caller="codex-cli")
        await manager.wait(view["job_id"])

        row = await kstore.get_job(view["job_id"])
        assert row["caller"] == "codex-cli"
        assert JobManager.describe(row)["caller"] == "codex-cli"

    @pytest.mark.asyncio
    async def test_distill_job_caller_falls_back_to_bound_context(self, kstore, monkeypatch):
        from src.utils import reset_active_caller, set_active_caller

        monkeypatch.setattr(JobManager, "_run_distill_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=kstore)

        token = set_active_caller("gemini-agent")
        try:
            view = await manager.submit_distill("sess-attr")
        finally:
            reset_active_caller(token)
        await manager.wait(view["job_id"])

        row = await kstore.get_job(view["job_id"])
        assert row["caller"] == "gemini-agent"

    @pytest.mark.asyncio
    async def test_distill_validates_empty_session(self, kstore):
        manager = JobManager(job_store=kstore)
        res = await manager.submit_distill("  ")
        assert "Input Validation Error" in res["error"]

    @pytest.mark.asyncio
    async def test_submit_distill_scopes_session_defense_in_depth(
        self, kstore, monkeypatch
    ):
        from src.identity import (
            _ACTIVE_CLIENT_ID,
            reset_active_principal,
            set_active_principal,
        )

        monkeypatch.setattr(JobManager, "_run_distill_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=kstore)
        principal = set_active_principal("http:anon")
        client = _ACTIVE_CLIENT_ID.set("cursor")
        try:
            view = await manager.submit_distill("sess-scoped")
        finally:
            _ACTIVE_CLIENT_ID.reset(client)
            reset_active_principal(principal)
        await manager.wait(view["job_id"])
        row = await kstore.get_job(view["job_id"])
        assert row["prompt"] == "[distill] session:http%3Aanon:cursor:sess-scoped"


class TestAutoDistill:
    @pytest.mark.asyncio
    async def test_off_by_default(self, kstore, monkeypatch):
        monkeypatch.delenv("UNIGROK_AUTO_DISTILL", raising=False)
        monkeypatch.setattr(utils_module, "_AUTO_DISTILLED_SESSIONS", set())
        manager = MagicMock()
        manager.submit_distill = AsyncMock()
        monkeypatch.setattr("src.jobs.get_job_manager", lambda: manager)

        history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
        await append_and_save_history("auto-off", history, "q", "a", kstore)
        manager.submit_distill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_submits_once_per_session_past_threshold(self, kstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_AUTO_DISTILL", "1")
        monkeypatch.setenv("UNIGROK_AUTO_DISTILL_MIN_MESSAGES", "4")
        monkeypatch.setattr(utils_module, "_AUTO_DISTILLED_SESSIONS", set())
        manager = MagicMock()
        manager.submit_distill = AsyncMock(return_value={"job_id": "j1"})
        monkeypatch.setattr("src.jobs.get_job_manager", lambda: manager)

        history: list = []
        # Turn 1: history reaches 2 messages — below the threshold.
        await append_and_save_history("auto-on", history, "q1", "a1", kstore)
        manager.submit_distill.assert_not_awaited()
        # Turn 2: 4 messages — crosses the threshold, one submission.
        await append_and_save_history("auto-on", history, "q2", "a2", kstore)
        manager.submit_distill.assert_awaited_once_with("auto-on")
        # Turn 3: already distilled this process — no second submission.
        await append_and_save_history("auto-on", history, "q3", "a3", kstore)
        manager.submit_distill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_submit_failure_never_breaks_the_turn(self, kstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_AUTO_DISTILL", "1")
        monkeypatch.setenv("UNIGROK_AUTO_DISTILL_MIN_MESSAGES", "2")
        monkeypatch.setattr(utils_module, "_AUTO_DISTILLED_SESSIONS", set())
        manager = MagicMock()
        manager.submit_distill = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr("src.jobs.get_job_manager", lambda: manager)

        history: list = []
        await append_and_save_history("auto-err", history, "q", "a", kstore)
        assert len(history) == 2  # the turn itself completed


# ─────────────────────────────────────────────────────────────────────────────
# get_dynamic_context rework: ranked candidate files + knowledge injection
# ─────────────────────────────────────────────────────────────────────────────

def _fake_git(monkeypatch, status_lines: bytes, branch: bytes = b"main\n"):
    class FakeProc:
        def __init__(self, out):
            self._out = out
            self.returncode = 0

        async def communicate(self):
            return self._out, b""

    def fake_exec(*cmd, **kwargs):
        async def _spawn():
            if "status" in cmd:
                return FakeProc(status_lines)
            if "--abbrev-ref" in cmd:
                return FakeProc(branch)
            return FakeProc(b"abcdef123456\n")
        return _spawn()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


class TestRankedContextFile:
    @pytest.mark.asyncio
    async def test_prompt_ranks_all_modified_files(self, tmp_path, monkeypatch):
        """The known weakness fix: with several modified files, the one whose
        path+head overlaps the prompt terms wins — not simply the first."""
        from src.utils import get_dynamic_context

        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))
        (tmp_path / "alpha.py").write_text("unrelated telemetry counters\n", encoding="utf-8")
        (tmp_path / "beta.py").write_text("worker queue processing logic\n", encoding="utf-8")
        _fake_git(monkeypatch, b"M  alpha.py\nM  beta.py\n")

        try:
            git_cache.clear()
            context, injected, _ = await get_dynamic_context(
                prompt="fix the beta worker queue bug"
            )
        finally:
            git_cache.clear()

        assert injected is True
        assert "beta.py" in context
        assert "worker queue processing logic" in context
        assert "Current/Active File" in context

    @pytest.mark.asyncio
    async def test_promptless_call_keeps_first_modified_file(self, tmp_path, monkeypatch):
        from src.utils import get_dynamic_context

        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))
        (tmp_path / "alpha.py").write_text("first file\n", encoding="utf-8")
        (tmp_path / "beta.py").write_text("second file\n", encoding="utf-8")
        _fake_git(monkeypatch, b"M  alpha.py\nM  beta.py\n")

        try:
            git_cache.clear()
            context, injected, _ = await get_dynamic_context()
        finally:
            git_cache.clear()

        assert injected is True
        assert "alpha.py" in context

    @pytest.mark.asyncio
    async def test_zero_overlap_falls_back_to_first_candidate(self, tmp_path, monkeypatch):
        from src.utils import get_dynamic_context

        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))
        (tmp_path / "alpha.py").write_text("first file\n", encoding="utf-8")
        (tmp_path / "beta.py").write_text("second file\n", encoding="utf-8")
        _fake_git(monkeypatch, b"M  alpha.py\nM  beta.py\n")

        try:
            git_cache.clear()
            context, _, _ = await get_dynamic_context(prompt="совершенно unrelated щ")
        finally:
            git_cache.clear()

        assert "alpha.py" in context


class TestKnowledgeContextInjection:
    @pytest.fixture
    async def ctx_store(self, tmp_path, monkeypatch):
        """Fresh store swapped in as the module-global get_dynamic_context
        reads facts from, with a fake single-file workspace around it."""
        s = GrokSessionStore(db_path=tmp_path / "ctx.db")
        monkeypatch.setattr(utils_module, "store", s)
        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))
        (tmp_path / "app.py").write_text("gateway startup code\n", encoding="utf-8")
        _fake_git(monkeypatch, b"M  app.py\n")
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_matching_facts_injected_marked_and_touched(self, ctx_store):
        from src.utils import get_dynamic_context

        fid = await ctx_store.save_fact("The gateway streams SSE with keepalive pings")
        await ctx_store.save_fact("Unrelated database vacuum runs weekly elsewhere")

        try:
            git_cache.clear()
            context, _, _ = await get_dynamic_context(
                prompt="why does the gateway drop SSE streams?"
            )
        finally:
            git_cache.clear()

        assert "# Workspace Knowledge" in context
        assert "[Workspace knowledge]" in context
        assert "hints to verify" in context
        assert "keepalive pings" in context
        # touch_facts ran for the injected fact.
        rows = {r["id"]: r for r in await ctx_store.list_facts()}
        assert rows[fid]["uses"] == 1

    @pytest.mark.asyncio
    async def test_injection_bounded_by_top_k_env(self, ctx_store, monkeypatch):
        from src.utils import get_dynamic_context

        for i in range(6):
            await ctx_store.save_fact(f"gateway retry detail number {i} for sse streams")
        monkeypatch.setenv("UNIGROK_KNOWLEDGE_TOP_K", "2")

        try:
            git_cache.clear()
            context, _, _ = await get_dynamic_context(prompt="gateway sse retry streams")
        finally:
            git_cache.clear()

        knowledge_block = context.split("# Workspace Knowledge", 1)[1]
        bullet_count = sum(1 for line in knowledge_block.splitlines() if line.startswith("- "))
        assert bullet_count == 2

        # UNIGROK_KNOWLEDGE_TOP_K=0 disables the block entirely.
        monkeypatch.setenv("UNIGROK_KNOWLEDGE_TOP_K", "0")
        try:
            git_cache.clear()
            context, _, _ = await get_dynamic_context(prompt="gateway sse retry streams")
        finally:
            git_cache.clear()
        assert "# Workspace Knowledge" not in context

    @pytest.mark.asyncio
    async def test_no_block_without_prompt_or_matches(self, ctx_store):
        from src.utils import get_dynamic_context

        await ctx_store.save_fact("The gateway streams SSE with keepalive pings")
        try:
            git_cache.clear()
            promptless, _, _ = await get_dynamic_context()
            git_cache.clear()
            unmatched, _, _ = await get_dynamic_context(prompt="quantum chromodynamics lattice")
        finally:
            git_cache.clear()
        assert "# Workspace Knowledge" not in promptless
        assert "# Workspace Knowledge" not in unmatched

    @pytest.mark.asyncio
    async def test_context_id_stable_regardless_of_knowledge(self, ctx_store):
        """The partition key is workspace-only: adding facts changes the
        context text but never the context_id (computed pre-injection)."""
        from src.utils import get_dynamic_context

        try:
            git_cache.clear()
            ctx_before, _, cid_before = await get_dynamic_context(
                prompt="why does the gateway drop SSE streams?"
            )
            await ctx_store.save_fact("The gateway streams SSE with keepalive pings")
            git_cache.clear()
            ctx_after, _, cid_after = await get_dynamic_context(
                prompt="why does the gateway drop SSE streams?"
            )
        finally:
            git_cache.clear()

        assert cid_before == cid_after
        assert ctx_before != ctx_after
        assert "# Workspace Knowledge" in ctx_after

    @pytest.mark.asyncio
    async def test_prompt_aware_cache_keys_are_isolated(self, ctx_store):
        from src.utils import get_dynamic_context

        await ctx_store.save_fact("The gateway streams SSE with keepalive pings")
        try:
            git_cache.clear()
            with_facts, _, _ = await get_dynamic_context(prompt="gateway sse streams")
            promptless, _, _ = await get_dynamic_context()
            # Same prompt within the TTL returns the cached tuple untouched.
            cached_again, _, _ = await get_dynamic_context(prompt="gateway sse streams")
        finally:
            git_cache.clear()
        assert "# Workspace Knowledge" in with_facts
        assert "# Workspace Knowledge" not in promptless
        assert cached_again == with_facts

    @pytest.mark.asyncio
    async def test_store_failure_never_breaks_context(self, ctx_store, monkeypatch):
        from src.utils import get_dynamic_context

        monkeypatch.setattr(
            ctx_store, "search_facts", AsyncMock(side_effect=RuntimeError("db gone"))
        )
        try:
            git_cache.clear()
            context, injected, context_id = await get_dynamic_context(prompt="gateway sse")
        finally:
            git_cache.clear()
        assert context_id.startswith("ctx-")
        assert "# Workspace Knowledge" not in context

    def test_format_knowledge_notes_shape(self):
        assert format_knowledge_notes([]) == ""
        assert format_knowledge_notes([{"fact": "   "}]) == ""
        notes = format_knowledge_notes([{"fact": "x" * 500}, {"fact": "short one"}])
        assert notes.startswith("# Workspace Knowledge")
        assert "[Workspace knowledge]" in notes
        # Per-fact clamp keeps the block bounded.
        assert "- " + "x" * 300 in notes
        assert "x" * 301 not in notes


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge MCP tools + grok://knowledge resource
# ─────────────────────────────────────────────────────────────────────────────

class TestKnowledgeTools:
    @pytest.fixture
    async def tool_store(self, tmp_path, monkeypatch):
        s = GrokSessionStore(db_path=tmp_path / "tools.db")
        monkeypatch.setattr("src.tools.knowledge.store", s)
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_remember_search_forget_roundtrip(self, tool_store):
        saved = await remember_fact("The eval harness replays cassettes offline")
        assert saved["status"] == "saved"
        assert saved["scope"] == "global"
        fact_id = saved["fact_id"]

        found = await search_knowledge("how do evals replay cassettes?")
        assert found["count"] == 1
        assert found["facts"][0]["id"] == fact_id
        assert "terms" not in found["facts"][0], "internal column stays internal"

        gone = await forget_fact(fact_id)
        assert gone["status"] == "deleted"
        assert (await forget_fact(fact_id))["status"] == "not_found"
        assert (await search_knowledge("evals cassettes"))["count"] == 0

    @pytest.mark.asyncio
    async def test_tool_input_validation(self, tool_store):
        assert "Input Validation Error" in (await remember_fact("  "))["error"]
        assert "Input Validation Error" in (await search_knowledge(""))["error"]
        assert "Input Validation Error" in (await forget_fact("nope"))["error"]
        assert "Input Validation Error" in (await distill_session(""))["error"]

    @pytest.mark.asyncio
    async def test_distill_session_submits_job(self, monkeypatch):
        manager = MagicMock()
        manager.submit_distill = AsyncMock(
            return_value={"job_id": "j-d", "status": "queued", "kind": "distill"}
        )
        monkeypatch.setattr("src.tools.knowledge.get_job_manager", lambda: manager)
        res = await distill_session("my-session")
        manager.submit_distill.assert_awaited_once_with("my-session", caller=None)
        assert res["job_id"] == "j-d"

    @pytest.mark.asyncio
    async def test_distill_session_forwards_ctx_caller(self, monkeypatch):
        """Same attribution as submit_research_job: the FastMCP-injected ctx's
        clientInfo name identifies which agent submitted the distill job."""
        manager = MagicMock()
        manager.submit_distill = AsyncMock(
            return_value={"job_id": "j-d2", "status": "queued", "kind": "distill"}
        )
        monkeypatch.setattr("src.tools.knowledge.get_job_manager", lambda: manager)
        ctx = SimpleNamespace(
            session=SimpleNamespace(
                client_params=SimpleNamespace(
                    clientInfo=SimpleNamespace(name="claude-code", version="1.2.3")
                )
            )
        )
        await distill_session("my-session", ctx=ctx)
        assert manager.submit_distill.call_args.kwargs["caller"] == "claude-code"

    @pytest.mark.asyncio
    async def test_distill_session_scopes_short_name_to_principal_client(
        self, monkeypatch
    ):
        """agent/chat store under principal:client:name; distill must match."""
        from src.identity import (
            _ACTIVE_CLIENT_ID,
            reset_active_principal,
            set_active_principal,
        )

        manager = MagicMock()
        manager.submit_distill = AsyncMock(
            return_value={"job_id": "j-scope", "status": "queued", "kind": "distill"}
        )
        monkeypatch.setattr("src.tools.knowledge.get_job_manager", lambda: manager)
        principal = set_active_principal("http:anon")
        client = _ACTIVE_CLIENT_ID.set("cursor")
        try:
            await distill_session("my-session")
        finally:
            _ACTIVE_CLIENT_ID.reset(client)
            reset_active_principal(principal)
        manager.submit_distill.assert_awaited_once_with(
            "http%3Aanon:cursor:my-session", caller=None
        )

    @pytest.mark.asyncio
    async def test_distill_session_reprefixes_foreign_fully_qualified_name(
        self, monkeypatch
    ):
        """A peer client's FQ session name must not be readable as-is."""
        from src.identity import (
            _ACTIVE_CLIENT_ID,
            reset_active_principal,
            set_active_principal,
        )

        manager = MagicMock()
        manager.submit_distill = AsyncMock(
            return_value={"job_id": "j-iso", "status": "queued", "kind": "distill"}
        )
        monkeypatch.setattr("src.tools.knowledge.get_job_manager", lambda: manager)
        principal = set_active_principal("http:anon")
        client = _ACTIVE_CLIENT_ID.set("vscode")
        try:
            await distill_session("http%3Aanon:cursor:peer-secret")
        finally:
            _ACTIVE_CLIENT_ID.reset(client)
            reset_active_principal(principal)
        submitted = manager.submit_distill.call_args.args[0]
        assert submitted.startswith("http%3Aanon:vscode:")
        assert submitted != "http%3Aanon:cursor:peer-secret"

    @pytest.mark.asyncio
    async def test_distill_session_ctx_hidden_from_schema(self):
        """FastMCP injects ctx via the Context annotation; it must not leak
        into the tool's public input schema."""
        from mcp.server.fastmcp import FastMCP

        probe = FastMCP("schema-probe")
        probe.add_tool(distill_session)
        tools = {tool.name: tool for tool in await probe.list_tools()}

        properties = tools["distill_session"].inputSchema.get("properties", {})
        assert "ctx" not in properties
        assert "session" in properties

    @pytest.mark.asyncio
    async def test_search_merges_collection_matches_deduped(self, tool_store, monkeypatch):
        await tool_store.save_fact("local fact about caching layers")
        remote = [
            {"fact": "local fact about caching layers", "score": 0.9, "origin": "collection", "file_id": "f1"},
            {"fact": "remote-only fact about cache eviction", "score": 0.5, "origin": "collection", "file_id": "f2"},
        ]
        monkeypatch.setattr(
            "src.tools.knowledge.search_knowledge_collection",
            AsyncMock(return_value=remote),
        )
        res = await search_knowledge("caching layers cache")
        origins = [item.get("origin") for item in res["facts"]]
        assert origins.count("collection") == 1, "duplicate text must not merge twice"
        assert any(item.get("fact") == "remote-only fact about cache eviction" for item in res["facts"])

    @pytest.mark.asyncio
    async def test_knowledge_tools_registered_with_annotations(self):
        from src.server import mcp

        tools = {tool.name: tool for tool in await mcp.list_tools()}
        for name in ("remember_fact", "search_knowledge", "forget_fact", "distill_session"):
            assert name in tools, f"{name} not registered"
        assert tools["search_knowledge"].annotations.readOnlyHint is True
        assert tools["forget_fact"].annotations.destructiveHint is True
        for mutating in ("remember_fact", "distill_session"):
            ann = tools[mutating].annotations
            assert ann is None or ann.readOnlyHint is not True

    @pytest.mark.asyncio
    async def test_knowledge_resource_lists_recent_facts(self, tmp_path, monkeypatch):
        from src.server import mcp

        s = GrokSessionStore(db_path=tmp_path / "res.db")
        monkeypatch.setattr("src.tools.resources.store", s)
        try:
            await s.save_fact("resource visible fact")
            uris = {str(res.uri) for res in await mcp.list_resources()}
            assert "grok://knowledge" in uris
            contents = list(await mcp.read_resource("grok://knowledge"))
            payload = json.loads(contents[0].content)
            assert payload["count"] == 1
            assert payload["facts"][0]["fact"] == "resource visible fact"
        finally:
            await s.close()


# ─────────────────────────────────────────────────────────────────────────────
# xAI collections adapter (UNIGROK_COLLECTIONS, capability-gated)
# ─────────────────────────────────────────────────────────────────────────────

def _fake_collections_client(collections=None, search_matches=None):
    service = MagicMock()
    service.list.return_value = SimpleNamespace(collections=list(collections or []))
    service.create.return_value = SimpleNamespace(collection_id="col-new")
    service.upload_document.return_value = SimpleNamespace()
    service.search.return_value = SimpleNamespace(matches=list(search_matches or []))
    client = MagicMock(spec=["collections"])
    client.collections = service
    return client, service


@pytest.fixture
def reset_collections_state(monkeypatch):
    monkeypatch.setattr(utils_module, "_KNOWLEDGE_COLLECTION_ID", None)
    monkeypatch.setattr(utils_module, "_COLLECTIONS_WARNED", False)


class TestCollectionsAdapter:
    @pytest.mark.asyncio
    async def test_disabled_by_default_never_touches_client(
        self, monkeypatch, reset_collections_state
    ):
        monkeypatch.delenv("UNIGROK_COLLECTIONS", raising=False)
        with patch("src.utils.get_xai_management_client") as mock_client:
            assert await sync_fact_to_collection(1, "fact") is False
            assert await search_knowledge_collection("query") == []
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_capability_gate_warns_once_and_degrades(
        self, monkeypatch, reset_collections_state
    ):
        monkeypatch.setenv("UNIGROK_COLLECTIONS", "1")
        incapable = MagicMock(spec=["chat"])  # no collections service at all
        with patch("src.utils.get_xai_management_client", return_value=incapable):
            assert await sync_fact_to_collection(1, "fact") is False
            assert utils_module._COLLECTIONS_WARNED is True
            assert await search_knowledge_collection("query") == []

    @pytest.mark.asyncio
    async def test_sync_finds_or_creates_named_collection_once(
        self, monkeypatch, reset_collections_state
    ):
        monkeypatch.setenv("UNIGROK_COLLECTIONS", "1")
        monkeypatch.setenv("UNIGROK_COLLECTION_NAME", "kb-test")
        client, service = _fake_collections_client()
        with patch("src.utils.get_xai_management_client", return_value=client):
            assert await sync_fact_to_collection(7, "durable fact text") is True
            assert await sync_fact_to_collection(8, "another fact") is True

        service.create.assert_called_once_with(name="kb-test")
        assert service.list.call_count == 1, "collection id must be cached"
        assert service.upload_document.call_count == 2
        args = service.upload_document.call_args_list[0].args
        assert args[0] == "col-new"
        assert args[1].startswith("fact-7-") and args[1].endswith(".txt")
        assert args[2] == b"durable fact text"

    @pytest.mark.asyncio
    async def test_sync_reuses_existing_collection(self, monkeypatch, reset_collections_state):
        monkeypatch.setenv("UNIGROK_COLLECTIONS", "1")
        monkeypatch.setenv("UNIGROK_COLLECTION_NAME", "kb-existing")
        existing = SimpleNamespace(collection_id="col-77", collection_name="kb-existing")
        client, service = _fake_collections_client(collections=[existing])
        with patch("src.utils.get_xai_management_client", return_value=client):
            assert await sync_fact_to_collection(1, "fact") is True
        service.create.assert_not_called()
        assert service.upload_document.call_args.args[0] == "col-77"

    @pytest.mark.asyncio
    async def test_search_passthrough_maps_matches(self, monkeypatch, reset_collections_state):
        monkeypatch.setenv("UNIGROK_COLLECTIONS", "1")
        matches = [
            SimpleNamespace(chunk_content="remote knowledge chunk", score=0.83, file_id="f-9"),
            SimpleNamespace(chunk_content="   ", score=0.5, file_id="f-x"),
        ]
        client, service = _fake_collections_client(search_matches=matches)
        with patch("src.utils.get_xai_management_client", return_value=client):
            results = await search_knowledge_collection("remote knowledge", limit=3)
        assert results == [{
            "fact": "remote knowledge chunk",
            "score": pytest.approx(0.83),
            "origin": "collection",
            "file_id": "f-9",
        }]
        assert service.search.call_args.args[1] == [service.create.return_value.collection_id]

    @pytest.mark.asyncio
    async def test_upstream_failure_logged_once_never_raises(
        self, monkeypatch, reset_collections_state
    ):
        monkeypatch.setenv("UNIGROK_COLLECTIONS", "1")
        client, service = _fake_collections_client()
        service.list.side_effect = RuntimeError("collections API down")
        with patch("src.utils.get_xai_management_client", return_value=client):
            assert await sync_fact_to_collection(1, "fact") is False
            assert await search_knowledge_collection("q") == []
        assert utils_module._COLLECTIONS_WARNED is True
