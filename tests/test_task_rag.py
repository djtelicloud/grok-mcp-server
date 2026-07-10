# tests/test_task_rag.py
# Semantic task-memory RAG (UNIGROK_TASK_RAG): task_memory_fts local index
# (dual-write, divergence repair, fallback parity), the src/rag.py module
# (config, TaskMemoryMirror, fusion, decision signal, evidence gathering),
# sync triggers, and the `rag` CLI. The advisor precedence tests live in
# tests/test_evals.py next to the existing calibration precedence pins.

import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.rag as rag
from src.rag import (
    SemanticVerdict,
    TaskMemoryMirror,
    fuse_task_evidence,
    gather_semantic_evidence,
    get_task_memory_mirror,
    get_task_rag_stats,
    semantic_route_signal,
    task_rag_mode,
)
from src.utils import GrokSessionStore

PLANNING = "grok-4.3"
CODING = "grok-build-0.1"


@pytest.fixture(autouse=True)
def _reset_rag_state():
    rag.reset_task_rag_state()
    yield
    rag.reset_task_rag_state()


def _fake_collections_client(collections=None, search_matches=None):
    """Same seam as tests/test_knowledge.py: a MagicMock collections service
    injected at the get_xai_client boundary (patched at src.rag here)."""
    service = MagicMock()
    service.list.return_value = SimpleNamespace(collections=list(collections or []))
    service.create.return_value = SimpleNamespace(collection_id="col-new")
    service.upload_document.return_value = SimpleNamespace()
    service.search.return_value = SimpleNamespace(matches=list(search_matches or []))
    client = MagicMock(spec=["collections"])
    client.collections = service
    return client, service


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


# ─────────────────────────────────────────────────────────────────────────────
# Config parsing (UNIGROK_TASK_RAG_*)
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskRagConfig:
    def test_mode_defaults_off(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_TASK_RAG", raising=False)
        assert task_rag_mode() == "off"

    @pytest.mark.parametrize("mode", ["off", "mirror", "shadow", "active"])
    def test_valid_modes_pass_through(self, monkeypatch, mode):
        monkeypatch.setenv("UNIGROK_TASK_RAG", mode)
        assert task_rag_mode() == mode

    def test_unknown_mode_warns_once_and_reads_off(self, monkeypatch, caplog):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "activ")
        with caplog.at_level("WARNING", logger="GrokMCP"):
            assert task_rag_mode() == "off"
            assert task_rag_mode() == "off"
        warnings = [r for r in caplog.records if "UNIGROK_TASK_RAG" in r.message]
        assert len(warnings) == 1

    def test_numeric_envs_are_clamped(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG_TOP_K", "99")
        monkeypatch.setenv("UNIGROK_TASK_RAG_MIN_EVIDENCE", "0")
        monkeypatch.setenv("UNIGROK_TASK_RAG_FUSION_LOCAL_WEIGHT", "7.5")
        monkeypatch.setenv("UNIGROK_TASK_RAG_FUSION_REMOTE_WEIGHT", "-2")
        assert rag.task_rag_top_k() == 10
        assert rag.task_rag_min_evidence() == 1
        assert rag.task_rag_fusion_weights() == (1.0, 0.0)

    def test_bad_numeric_envs_fall_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG_TOP_K", "many")
        monkeypatch.setenv("UNIGROK_TASK_RAG_HALF_LIFE_HOURS", "soon")
        assert rag.task_rag_top_k() == 5
        assert rag.task_rag_half_life_hours() == 168.0

    def test_collection_name_versioned_default(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_TASK_RAG_COLLECTION", raising=False)
        assert rag.task_rag_collection_name() == "unigrok-task-memories-v1"


# ─────────────────────────────────────────────────────────────────────────────
# TaskMemoryMirror: gating, find-or-create, upload, search, soft-disable
# ─────────────────────────────────────────────────────────────────────────────

def _memory_row(memory_id=1, **overrides):
    row = dict(
        id=memory_id,
        task_hash="abcdef0123456789",
        prompt_excerpt="fix the sqlite wal checkpoint",
        outcome_summary="patched the checkpoint interval",
        plane="API",
        model=CODING,
        profile="default",
        success=1,
        created_at=datetime.now().isoformat(),
    )
    row.update(overrides)
    return row


class TestTaskMemoryMirror:
    @pytest.mark.asyncio
    async def test_off_mode_never_touches_client(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_TASK_RAG", raising=False)
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client") as mock_client:
            assert await mirror.upload_memory(_memory_row()) is None
            assert await mirror.search("query", 5) == []
            assert await mirror.ready() is False
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_finds_or_creates_collection_once(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "mirror")
        monkeypatch.setenv("UNIGROK_TASK_RAG_COLLECTION", "tm-test")
        client, service = _fake_collections_client()
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            fid1 = await mirror.upload_memory(_memory_row(1))
            fid2 = await mirror.upload_memory(_memory_row(2))

        service.create.assert_called_once_with(name="tm-test")
        assert service.list.call_count == 1, "collection id must be cached"
        assert service.upload_document.call_count == 2
        # No file_id in the SDK response -> deterministic doc name IS the id.
        assert fid1 == "taskmem-1-abcdef01.txt"
        assert fid2 == "taskmem-2-abcdef01.txt"
        args = service.upload_document.call_args_list[0].args
        assert args[0] == "col-new"
        assert args[1] == "taskmem-1-abcdef01.txt"
        body = args[2].decode("utf-8")
        header, prose = body.split("\n", 1)
        import json as _json

        assert _json.loads(header) == {"memory_id": 1, "task_hash": "abcdef0123456789"}
        assert "fix the sqlite wal checkpoint" in prose
        assert "patched the checkpoint interval" in prose

    @pytest.mark.asyncio
    async def test_upload_prefers_sdk_file_id(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "mirror")
        client, service = _fake_collections_client()
        service.upload_document.return_value = SimpleNamespace(file_id="fid-42")
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            assert await mirror.upload_memory(_memory_row()) == "fid-42"

    @pytest.mark.asyncio
    async def test_reuses_existing_collection(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "mirror")
        monkeypatch.setenv("UNIGROK_TASK_RAG_COLLECTION", "tm-existing")
        existing = SimpleNamespace(collection_id="col-77", collection_name="tm-existing")
        client, service = _fake_collections_client(collections=[existing])
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            await mirror.upload_memory(_memory_row())
        service.create.assert_not_called()
        assert service.upload_document.call_args.args[0] == "col-77"

    @pytest.mark.asyncio
    async def test_search_maps_matches_and_bounds_query(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "shadow")
        matches = [
            SimpleNamespace(chunk_content="remote chunk", score=0.9, file_id="f-1"),
            SimpleNamespace(chunk_content="  ", score=0.5, file_id="f-2"),
        ]
        client, service = _fake_collections_client(search_matches=matches)
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            results = await mirror.search("query " * 200, 5)
        assert results == [
            {"content": "remote chunk", "score": pytest.approx(0.9), "file_id": "f-1"}
        ]
        # Query is bounded before it leaves the process.
        sent_query = service.search.call_args.args[0]
        assert len(sent_query) < 600

    @pytest.mark.asyncio
    async def test_soft_disable_backoff_doubles_and_cooldown_reenables(
        self, monkeypatch
    ):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "mirror")
        client, service = _fake_collections_client()
        service.list.side_effect = RuntimeError("collections API down")
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            for _ in range(5):
                assert await mirror.upload_memory(_memory_row()) is None
            # First trip: ~30s cooldown, calls now short-circuit.
            first_cooldown = mirror.cooldown_remaining_sec()
            assert 0.0 < first_cooldown <= 30.0
            calls_after_trip = service.list.call_count
            assert await mirror.upload_memory(_memory_row()) is None
            assert service.list.call_count == calls_after_trip

            # Cooldown expiry re-enables; a second trip doubles the backoff.
            mirror._disabled_until = 0.0
            for _ in range(5):
                assert await mirror.upload_memory(_memory_row()) is None
            assert mirror.cooldown_remaining_sec() > first_cooldown

            # Success resets both the counter and the backoff ladder.
            mirror._disabled_until = 0.0
            service.list.side_effect = None
            assert await mirror.upload_memory(_memory_row()) is not None
            assert mirror._trips == 0
            assert mirror.last_known_ready is True

    @pytest.mark.asyncio
    async def test_search_token_bucket_exhaustion_fails_open(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "shadow")
        client, service = _fake_collections_client()
        mirror = get_task_memory_mirror()
        mirror._bucket_tokens = 0.0
        mirror._bucket_refreshed = time.monotonic()
        with patch("src.rag.get_xai_client", return_value=client):
            assert await mirror.search("query", 5) == []
        service.search.assert_not_called()
        assert get_task_rag_stats()["rate_limited"] == 1

    @pytest.mark.asyncio
    async def test_sync_pending_drains_and_marks(self, monkeypatch, tstore):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "mirror")
        await _save_memory(tstore, "first unsynced task about parsers")
        await _save_memory(tstore, "second unsynced task about caching")
        client, service = _fake_collections_client()
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            summary = await mirror.sync_pending(tstore, limit=10)
        assert summary == {"synced": 2, "failed": 0}
        assert await tstore.count_unsynced_task_memories() == 0
        # remote_file_id stored the doc-name identity (no SDK file_id) and
        # maps back to the local row.
        async with tstore._conn.execute(
            "SELECT id, remote_file_id FROM task_memory ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
        assert len(rows) == 2
        for row_id, remote_file_id in rows:
            assert remote_file_id.startswith(f"taskmem-{row_id}-")
            mapped = await tstore.get_task_memories_by_remote_ids([remote_file_id])
            assert [m["id"] for m in mapped] == [row_id]

    @pytest.mark.asyncio
    async def test_sync_pending_records_failures_and_respects_max_attempts(
        self, monkeypatch, tstore
    ):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "mirror")
        await _save_memory(tstore, "task that will fail to upload")
        client, service = _fake_collections_client()
        service.upload_document.side_effect = RuntimeError("quota exceeded")
        mirror = get_task_memory_mirror()
        with patch("src.rag.get_xai_client", return_value=client):
            summary = await mirror.sync_pending(tstore, limit=10)
        assert summary == {"synced": 0, "failed": 1}
        row = (await tstore.list_unsynced_task_memories())[0]
        assert row["sync_attempts"] == 1
        assert "quota exceeded" in row["sync_error"]
        # Exhausted rows drop out of the automatic drain window.
        assert await tstore.list_unsynced_task_memories(max_attempts=1) == []


# ─────────────────────────────────────────────────────────────────────────────
# Fusion + decision signal (pure functions)
# ─────────────────────────────────────────────────────────────────────────────

class TestFusion:
    def test_dedup_by_id_combines_local_and_remote(self):
        now = datetime.now()
        local = [
            {"id": 1, "score": 1.0, "created_at": now.isoformat(), "model": CODING, "success": 1},
            {"id": 2, "score": 0.5, "created_at": now.isoformat(), "model": CODING, "success": 1},
        ]
        remote = [
            {"id": 1, "remote_score": 0.9, "created_at": now.isoformat(), "model": CODING, "success": 1},
        ]
        fused = fuse_task_evidence(
            local, remote, top_k=5, half_life_hours=168.0,
            local_weight=0.65, remote_weight=0.35, now=now,
        )
        assert [e["id"] for e in fused] == [1, 2]
        # id 1: full local norm + full remote norm at zero age.
        assert fused[0]["fused_score"] == pytest.approx(0.65 + 0.35)
        assert fused[1]["fused_score"] == pytest.approx(0.65 * 0.5)

    def test_recency_halves_remote_component_at_half_life(self):
        now = datetime.now()
        old = (now - timedelta(hours=168)).isoformat()
        remote = [
            {"id": 1, "remote_score": 1.0, "created_at": now.isoformat()},
            {"id": 2, "remote_score": 1.0, "created_at": old},
        ]
        fused = fuse_task_evidence(
            [], remote, top_k=5, half_life_hours=168.0,
            local_weight=0.65, remote_weight=0.35, now=now,
        )
        assert fused[0]["id"] == 1
        assert fused[0]["fused_score"] == pytest.approx(0.35)
        assert fused[1]["fused_score"] == pytest.approx(0.35 * 0.5)

    def test_unparsable_created_at_degrades_to_full_recency(self):
        fused = fuse_task_evidence(
            [], [{"id": 1, "remote_score": 1.0, "created_at": "not-a-date"}],
            top_k=5, half_life_hours=168.0, local_weight=0.65, remote_weight=0.35,
        )
        assert fused[0]["fused_score"] == pytest.approx(0.35)

    def test_top_k_caps_output(self):
        local = [{"id": i, "score": 1.0 / i, "created_at": None} for i in range(1, 9)]
        fused = fuse_task_evidence(
            local, [], top_k=3, half_life_hours=168.0,
            local_weight=0.65, remote_weight=0.35,
        )
        assert len(fused) == 3


class TestSemanticRouteSignal:
    def _fused(self, spec):
        return [
            {"id": i, "fused_score": w, "model": m, "success": s}
            for i, (w, m, s) in enumerate(spec, start=1)
        ]

    def test_planning_flip_when_margin_met(self):
        fused = self._fused([
            (1.0, PLANNING, 1), (0.8, PLANNING, 1), (0.9, CODING, 0),
        ])
        verdict = semantic_route_signal(
            fused, PLANNING, CODING, margin=0.15, min_evidence=3
        )
        assert verdict.prefers_planning is True
        assert verdict.planning_signal == pytest.approx(1.0)
        assert verdict.coding_signal == pytest.approx(0.0)
        assert verdict.evidence_count == 3
        assert 0.0 < verdict.confidence <= 1.0

    def test_decidable_false_when_coding_wins(self):
        fused = self._fused([
            (1.0, PLANNING, 0), (0.8, CODING, 1), (0.9, CODING, 1),
        ])
        verdict = semantic_route_signal(
            fused, PLANNING, CODING, margin=0.15, min_evidence=3
        )
        assert verdict.prefers_planning is False

    def test_undecidable_below_min_evidence(self):
        fused = self._fused([(1.0, PLANNING, 1), (0.9, CODING, 0)])
        verdict = semantic_route_signal(
            fused, PLANNING, CODING, margin=0.15, min_evidence=3
        )
        assert verdict.prefers_planning is None

    def test_undecidable_when_one_sided(self):
        fused = self._fused([
            (1.0, PLANNING, 1), (0.8, PLANNING, 1), (0.6, PLANNING, 1),
        ])
        verdict = semantic_route_signal(
            fused, PLANNING, CODING, margin=0.15, min_evidence=3
        )
        assert verdict.prefers_planning is None

    def test_other_models_do_not_count_as_evidence(self):
        fused = self._fused([
            (1.0, "grok-vision", 1), (0.8, PLANNING, 1),
            (0.7, CODING, 0), (0.6, CODING, 1),
        ])
        verdict = semantic_route_signal(
            fused, PLANNING, CODING, margin=0.15, min_evidence=3
        )
        assert verdict.evidence_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# Evidence gathering: mapping, cache, fail-open
# ─────────────────────────────────────────────────────────────────────────────

class TestGatherSemanticEvidence:
    @pytest.mark.asyncio
    async def test_end_to_end_maps_remote_hits_and_caches(self, monkeypatch, tstore):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "shadow")
        # Two synced memories the remote search will surface by file_id.
        await _save_memory(tstore, "plan the migration strategy carefully", model=PLANNING)
        await _save_memory(tstore, "plan the rollout of feature flags", model=PLANNING)
        await _save_memory(tstore, "plan the api deprecation timeline", model=CODING, success=0)
        await tstore.mark_task_memory_synced(1, "fid-1")
        await tstore.mark_task_memory_synced(2, "fid-2")
        await tstore.mark_task_memory_synced(3, "fid-3")

        matches = [
            SimpleNamespace(chunk_content="chunk one", score=0.9, file_id="fid-1"),
            SimpleNamespace(chunk_content="chunk two", score=0.8, file_id="fid-2"),
            SimpleNamespace(chunk_content="chunk three", score=0.7, file_id="fid-3"),
            SimpleNamespace(chunk_content="orphan", score=0.6, file_id="fid-unknown"),
        ]
        client, service = _fake_collections_client(search_matches=matches)
        with patch("src.rag.get_xai_client", return_value=client):
            verdict = await gather_semantic_evidence(
                tstore, "plan the migration", None, PLANNING, CODING
            )
            again = await gather_semantic_evidence(
                tstore, "plan the migration", None, PLANNING, CODING
            )

        assert isinstance(verdict, SemanticVerdict)
        assert verdict.prefers_planning is True  # planning succeeded, coding failed
        assert verdict.remote_count == 3  # the orphan hit was dropped
        assert again == verdict
        assert service.search.call_count == 1, "second gather must hit the 30s cache"
        stats = get_task_rag_stats()
        assert stats["queries"] == 2
        assert stats["cache_hits"] == 1
        assert stats["fused_score_count"] == 1

    @pytest.mark.asyncio
    async def test_header_fallback_maps_unmatched_file_id(self, monkeypatch, tstore):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "shadow")
        await _save_memory(tstore, "tune the query planner statistics")
        # Synced under one id, but the search returns a DIFFERENT chunk file
        # id — the JSON header inside the chunk recovers the identity.
        await tstore.mark_task_memory_synced(1, "fid-original")
        header_chunk = '{"memory_id":1,"task_hash":"deadbeef"}\nprose body'
        matches = [
            SimpleNamespace(chunk_content=header_chunk, score=0.9, file_id="fid-other"),
        ]
        client, _service = _fake_collections_client(search_matches=matches)
        with patch("src.rag.get_xai_client", return_value=client):
            verdict = await gather_semantic_evidence(
                tstore, "tune the query planner", None, PLANNING, CODING
            )
        assert verdict is not None
        assert verdict.remote_count == 1

    @pytest.mark.asyncio
    async def test_store_failure_fails_open_to_none(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")

        class _BrokenStore:
            async def get_similar_task_memories(self, *a, **k):
                raise RuntimeError("db locked")

        verdict = await gather_semantic_evidence(
            _BrokenStore(), "prompt", None, PLANNING, CODING
        )
        assert verdict is None
