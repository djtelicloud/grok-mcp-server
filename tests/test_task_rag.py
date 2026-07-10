# tests/test_task_rag.py
# Semantic task-memory RAG (UNIGROK_TASK_RAG): task_memory_fts local index
# (dual-write, divergence repair, fallback parity), the src/rag.py module
# (config, TaskMemoryMirror, fusion, decision signal, evidence gathering),
# sync triggers, and the `rag` CLI. The advisor precedence tests live in
# tests/test_evals.py next to the existing calibration precedence pins.

from unittest.mock import patch

import pytest

from src.utils import GrokSessionStore


@pytest.fixture
async def tstore(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "task_rag.db")
    yield s
    await s.close()


async def _save_memory(store, prompt, **overrides):
    defaults = dict(
        outcome_summary="done",
        plane="API",
        model="grok-build-0.1",
        profile="default",
        success=1,
        latency=0.5,
        cost=0.01,
    )
    defaults.update(overrides)
    return await store.save_task_memory(prompt=prompt, **defaults)


# ─────────────────────────────────────────────────────────────────────────────
# task_memory_fts: setup, dual-write, divergence repair
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskMemoryFTSSetup:
    @pytest.mark.asyncio
    async def test_fts_table_created_and_flagged(self, tstore):
        await tstore._ensure_initialized()
        assert tstore._task_memory_fts is True
        async with tstore._conn.execute(
            "SELECT name FROM sqlite_master WHERE name='task_memory_fts';"
        ) as cursor:
            assert await cursor.fetchone() is not None

    @pytest.mark.asyncio
    async def test_probe_failure_flags_fallback(self, tmp_path, monkeypatch):
        async def _no_fts(conn):
            return False

        monkeypatch.setattr(GrokSessionStore, "_probe_fts5", staticmethod(_no_fts))
        s = GrokSessionStore(db_path=tmp_path / "nofts.db")
        try:
            await s._ensure_initialized()
            assert s._task_memory_fts is False
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_divergence_rebuilds_index_on_reopen(self, tmp_path):
        """Rows written while FTS5 was unavailable must be indexed by the
        next init on a build WITH FTS5 (row-by-row divergence -> rebuild)."""
        db_path = tmp_path / "diverged.db"

        async def _no_fts(conn):
            return False

        with patch.object(GrokSessionStore, "_probe_fts5", staticmethod(_no_fts)):
            s1 = GrokSessionStore(db_path=db_path)
            await _save_memory(s1, "refactor the websocket reconnect backoff logic")
            assert s1._task_memory_fts is False
            await s1.close()

        s2 = GrokSessionStore(db_path=db_path)
        try:
            await s2._ensure_initialized()
            assert s2._task_memory_fts is True
            async with s2._conn.execute(
                "SELECT COUNT(*) FROM task_memory_fts"
            ) as cursor:
                row = await cursor.fetchone()
            assert row[0] == 1
            results = await s2.get_similar_task_memories(
                "websocket reconnect backoff"
            )
            assert len(results) == 1
        finally:
            await s2.close()


# ─────────────────────────────────────────────────────────────────────────────
# FTS-first get_similar_task_memories: score contract + bonuses
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskMemoryFTSRetrieval:
    @pytest.mark.asyncio
    async def test_save_returns_id_and_retrieval_ranks_match_first(self, tstore):
        mid = await _save_memory(tstore, "fix the sqlite wal checkpoint contention")
        assert isinstance(mid, int)
        await _save_memory(tstore, "design the streaming http gateway auth")
        await _save_memory(tstore, "summarize the release notes for docs")

        results = await tstore.get_similar_task_memories(
            "why does the sqlite wal checkpoint block?"
        )
        assert results
        assert results[0]["id"] == mid
        assert results == sorted(results, key=lambda i: i["score"], reverse=True)

    @pytest.mark.asyncio
    async def test_scores_normalized_before_bonuses(self, tstore):
        """Without context/hash bonuses, FTS scores land in the same 0..1
        band as the fallback's term-overlap fraction."""
        await _save_memory(tstore, "profile the vector cache eviction policy")
        await _save_memory(tstore, "profile the query planner statistics")

        results = await tstore.get_similar_task_memories("profile the cache eviction")
        assert results
        assert all(0.0 < item["score"] <= 1.0 for item in results)

    @pytest.mark.asyncio
    async def test_context_id_bonus_and_zero_overlap_recall(self, tstore):
        """A same-context row with ZERO term overlap must still surface
        (score 0 + 2.0 bonus) — the long-standing fallback semantics that
        the FTS path preserves via the context union."""
        await _save_memory(
            tstore, "migrate billing exports to parquet", context_id="ctx-42"
        )
        results = await tstore.get_similar_task_memories(
            "completely unrelated kernel scheduling question", context_id="ctx-42"
        )
        assert len(results) == 1
        assert results[0]["context_id"] == "ctx-42"
        assert results[0]["score"] >= 2.0

    @pytest.mark.asyncio
    async def test_task_hash_bonus_applied(self, tstore):
        prompt = "diagnose the flaky integration test in ci"
        await _save_memory(tstore, prompt)
        results = await tstore.get_similar_task_memories(prompt)
        assert results
        # normalized fts (<=1.0) + 1.0 hash bonus
        assert results[0]["score"] > 1.0

    @pytest.mark.asyncio
    async def test_metadata_decoded_to_dict(self, tstore):
        await _save_memory(
            tstore,
            "escalated planning task about architecture",
            metadata={"escalated": True},
        )
        results = await tstore.get_similar_task_memories("planning architecture task")
        assert results
        assert results[0]["metadata"] == {"escalated": True}

    @pytest.mark.asyncio
    async def test_empty_prompt_with_context_still_recalls(self, tstore):
        await _save_memory(tstore, "tune the retry budget", context_id="ctx-empty")
        results = await tstore.get_similar_task_memories("", context_id="ctx-empty")
        assert len(results) == 1
        assert results[0]["score"] >= 2.0


class TestTaskMemoryFallbackParity:
    @pytest.mark.asyncio
    async def test_fallback_ranks_and_bonuses_match_contract(
        self, tmp_path, monkeypatch
    ):
        async def _no_fts(conn):
            return False

        monkeypatch.setattr(GrokSessionStore, "_probe_fts5", staticmethod(_no_fts))
        s = GrokSessionStore(db_path=tmp_path / "fallback.db")
        try:
            await _save_memory(s, "fix the sqlite wal checkpoint contention")
            await _save_memory(
                s, "migrate billing exports to parquet", context_id="ctx-42"
            )

            results = await s.get_similar_task_memories(
                "sqlite wal checkpoint blocked"
            )
            assert results
            assert results[0]["prompt_excerpt"].startswith("fix the sqlite")
            assert 0.0 < results[0]["score"] <= 1.0

            ctx = await s.get_similar_task_memories(
                "unrelated prompt", context_id="ctx-42"
            )
            assert len(ctx) == 1 and ctx[0]["score"] >= 2.0
        finally:
            await s.close()
