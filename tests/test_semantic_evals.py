# tests/test_semantic_evals.py
# Shadow semantic evals (UNIGROK_SEMANTIC_EVALS): deterministic sampler,
# testing-flag inertness, LLM-judge grading via _parse_structured, the
# attach_semantic_scores telemetry envelope update, budget gating, and the
# end-to-end run_agent_turn trigger. Observational only by contract — nothing
# here touches routing.

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.semantic_evals as se
from src.semantic_evals import (
    SemanticEvalVerdict,
    TrajectorySample,
    get_semantic_eval_stats,
    maybe_submit_semantic_eval,
    semantic_evals_mode,
    should_sample,
)
from src.utils import (
    GrokSessionStore,
    MetaLayer,
    reset_request_id,
    run_agent_turn,
    set_request_id,
)


@pytest.fixture(autouse=True)
def _reset_semantic_state():
    se.reset_semantic_evals_state()
    yield
    se.reset_semantic_evals_state()


@pytest.fixture
async def sstore(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "semantic_evals.db")
    yield s
    await s.close()


def _sample(**overrides):
    defaults = dict(
        request_id="rid-semantic-test",
        prompt="fix the failing test",
        final_answer="Done: the assertion now matches the new schema.",
        tool_trace=[{"tool_name": "run_local_tests", "success": True, "content": "1 passed"}],
        route="coding",
        model="grok-test",
        plane="API",
        finish_reason="final_answer",
        latency_sec=1.2,
        cost_usd=0.01,
        caller="pytest",
    )
    defaults.update(overrides)
    return TrajectorySample(**defaults)


def _verdict(correctness=4, tool_efficiency=5, safety=5, rationale="minor gap"):
    return SemanticEvalVerdict(
        correctness=correctness,
        tool_efficiency=tool_efficiency,
        safety=safety,
        rationale=rationale,
    )


def _patch_judge(monkeypatch, verdict, tokens=100, cost=0.001):
    monkeypatch.setattr(
        "src.semantic_evals._parse_structured",
        AsyncMock(return_value=(verdict, tokens, cost)),
    )
    monkeypatch.setattr(
        "src.semantic_evals.resolve_model", AsyncMock(return_value="grok-test")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sampler determinism
# ─────────────────────────────────────────────────────────────────────────────

class TestSampler:
    def test_deterministic_per_request_id(self):
        verdicts = {should_sample("rid-7", 0.3) for _ in range(5)}
        assert len(verdicts) == 1  # same id → same verdict every time

    def test_rate_extremes(self):
        assert not should_sample("rid-x", 0.0)
        assert should_sample("rid-x", 1.0)
        assert not should_sample("", 1.0)

    def test_rate_band_over_fixed_ids(self):
        hits = sum(1 for i in range(1000) if should_sample(f"rid-{i}", 0.05))
        # Loose band: the hash is uniform-ish, not exact.
        assert 20 <= hits <= 90


# ─────────────────────────────────────────────────────────────────────────────
# Mode + gates
# ─────────────────────────────────────────────────────────────────────────────

class TestModeAndGates:
    def test_mode_defaults_off(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_SEMANTIC_EVALS", raising=False)
        assert semantic_evals_mode() == "off"

    def test_unknown_mode_warns_once_and_reads_off(self, monkeypatch, caplog):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "bogus")
        with caplog.at_level("WARNING", logger="GrokMCP"):
            assert semantic_evals_mode() == "off"
            assert semantic_evals_mode() == "off"
        warnings = [r for r in caplog.records if "UNIGROK_SEMANTIC_EVALS" in r.message]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_inert_under_testing_env_without_override(self, sstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        # conftest forces UNI_GROK_TESTING=1; no override → inert.
        assert maybe_submit_semantic_eval(_sample(), sstore) is None
        assert get_semantic_eval_stats()["sampled"] == 0

    @pytest.mark.asyncio
    async def test_testing_override_enables_sampling(self, sstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        _patch_judge(monkeypatch, _verdict())
        se.set_testing_override(True)
        task = maybe_submit_semantic_eval(_sample(), sstore)
        assert task is not None
        await se.wait_for_pending()
        assert get_semantic_eval_stats()["sampled"] == 1

    @pytest.mark.asyncio
    async def test_off_mode_never_samples(self, sstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "off")
        se.set_testing_override(True)
        assert maybe_submit_semantic_eval(_sample(), sstore) is None

    @pytest.mark.asyncio
    async def test_skips_ungradeable_outcomes(self, sstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        se.set_testing_override(True)
        assert maybe_submit_semantic_eval(_sample(final_answer=""), sstore) is None
        assert maybe_submit_semantic_eval(_sample(finish_reason="error"), sstore) is None
        assert maybe_submit_semantic_eval(_sample(finish_reason="unknown"), sstore) is None
        assert maybe_submit_semantic_eval(_sample(request_id=""), sstore) is None

    @pytest.mark.asyncio
    async def test_daily_budget_blocks_judge_calls(self, sstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_DAILY_BUDGET_USD", "0")
        se.set_testing_override(True)
        assert maybe_submit_semantic_eval(_sample(), sstore) is None
        stats = get_semantic_eval_stats()
        assert stats["budget_blocked"] == 1
        assert stats["sampled"] == 0

    @pytest.mark.asyncio
    async def test_budget_reservation_blocks_concurrent_overspend(self, sstore, monkeypatch):
        """Each in-flight judge call reserves the per-call cap BEFORE running,
        so N concurrent calls can never collectively overrun the budget."""
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_DAILY_BUDGET_USD", "0.03")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_MAX_COST_PER_CALL", "0.02")
        se.set_testing_override(True)
        import asyncio

        gate = asyncio.Event()

        async def _slow_parse(*args, **kwargs):
            await gate.wait()
            return (_verdict(), 100, 0.001)

        monkeypatch.setattr("src.semantic_evals._parse_structured", _slow_parse)
        monkeypatch.setattr(
            "src.semantic_evals.resolve_model", AsyncMock(return_value="grok-test")
        )
        await sstore.save_telemetry("a", "API", 1, 1.0, 0.0, request_id="rid-c1")

        first = maybe_submit_semantic_eval(_sample(request_id="rid-c1"), sstore)
        assert first is not None  # reserves 0.02 of the 0.03 budget
        second = maybe_submit_semantic_eval(_sample(request_id="rid-c2"), sstore)
        assert second is None  # 0.02 reserved + 0.02 would exceed 0.03

        gate.set()
        await se.wait_for_pending()
        stats = get_semantic_eval_stats()
        assert stats["budget_blocked"] == 1
        assert stats["budget_reserved"] == pytest.approx(0.0)  # settled
        assert stats["judge_cost_usd_today"] == pytest.approx(0.001)

    @pytest.mark.asyncio
    async def test_budget_hydrates_from_durable_record_across_restart(self, sstore, monkeypatch):
        """A restart resets the in-process accumulator, but the first sampled
        call floors it at the telemetry record — an exhausted budget stays
        exhausted."""
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_DAILY_BUDGET_USD", "1.0")
        se.set_testing_override(True)
        # Durable record: 2.00 USD already spent today (pre-"restart").
        await sstore.save_telemetry("old", "API", 1, 1.0, 0.0, request_id="rid-old")
        await sstore.attach_semantic_scores(
            "rid-old", {"v": 1, "judge_cost_usd": 2.0, "scores": {}}
        )
        parse_mock = AsyncMock(return_value=(_verdict(), 100, 0.001))
        monkeypatch.setattr("src.semantic_evals._parse_structured", parse_mock)
        monkeypatch.setattr(
            "src.semantic_evals.resolve_model", AsyncMock(return_value="grok-test")
        )

        task = maybe_submit_semantic_eval(_sample(), sstore)
        assert task is not None  # in-process accumulator is empty at the gate
        await se.wait_for_pending()

        parse_mock.assert_not_called()  # hydration blocked it before the call
        stats = get_semantic_eval_stats()
        assert stats["budget_blocked"] == 1
        assert stats["graded"] == 0
        assert stats["judge_cost_usd_today"] == pytest.approx(2.0)


# ─────────────────────────────────────────────────────────────────────────────
# Judge grading + recording
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeAndRecord:
    @pytest.mark.asyncio
    async def test_writes_bounded_semantic_block(self, sstore, monkeypatch):
        _patch_judge(monkeypatch, _verdict(), cost=0.0012)
        await sstore.save_telemetry(
            "fix the failing test", "API", 1, 1.2, 0.01,
            caller="pytest", request_id="rid-semantic-test", model="grok-test",
        )

        await se._grade_and_record(_sample(), sstore)

        rows = await sstore.get_telemetry_stats()
        meta = json.loads(rows[0]["metadata"])
        # Existing envelope keys survive the read-modify-write.
        assert meta["caller"] == "pytest"
        assert meta["request_id"] == "rid-semantic-test"
        semantic = meta["semantic"]
        assert semantic["v"] == 1
        assert semantic["mode"] == "shadow"
        assert semantic["scores"] == {"correctness": 4, "tool_efficiency": 5, "safety": 5}
        assert semantic["overall"] == pytest.approx(4.67, abs=0.01)
        assert semantic["judge_model"] == "grok-test"
        assert semantic["judge_cost_usd"] == pytest.approx(0.0012)
        stats = get_semantic_eval_stats()
        assert stats["graded"] == 1
        assert stats["avg_scores"]["overall"] == pytest.approx(4.67, abs=0.01)
        assert stats["judge_cost_usd_today"] == pytest.approx(0.0012)

    @pytest.mark.asyncio
    async def test_judge_failure_degrades_cleanly(self, sstore, monkeypatch):
        _patch_judge(monkeypatch, None, tokens=0, cost=0.0)
        await sstore.save_telemetry(
            "prompt", "API", 1, 1.0, 0.0, request_id="rid-semantic-test"
        )

        await se._grade_and_record(_sample(), sstore)

        rows = await sstore.get_telemetry_stats()
        assert "semantic" not in json.loads(rows[0]["metadata"])
        stats = get_semantic_eval_stats()
        assert stats["judge_failures"] == 1
        assert stats["graded"] == 0

    @pytest.mark.asyncio
    async def test_rationale_is_redacted_and_bounded(self, sstore, monkeypatch):
        leaky = "leaked xai-abcdefgh12345678 key " + "x" * 400
        _patch_judge(monkeypatch, _verdict(rationale=leaky))
        await sstore.save_telemetry(
            "prompt", "API", 1, 1.0, 0.0, request_id="rid-semantic-test"
        )

        await se._grade_and_record(_sample(), sstore)

        rows = await sstore.get_telemetry_stats()
        rationale = json.loads(rows[0]["metadata"])["semantic"]["rationale"]
        assert "xai-abcdefgh12345678" not in rationale
        assert "[REDACTED_KEY]" in rationale
        assert len(rationale) <= 300 + len("\n[...truncated 99999 chars]")

    @pytest.mark.asyncio
    async def test_attach_miss_is_counted(self, sstore, monkeypatch):
        _patch_judge(monkeypatch, _verdict())
        # No telemetry row exists for this request id.
        await se._grade_and_record(_sample(request_id="rid-missing"), sstore)
        stats = get_semantic_eval_stats()
        assert stats["attach_misses"] == 1
        assert stats["graded"] == 0

    @pytest.mark.asyncio
    async def test_open_breaker_skips_judge_readonly(self, sstore, monkeypatch):
        monkeypatch.setattr(
            "src.semantic_evals.resolve_model", AsyncMock(return_value="grok-test")
        )
        monkeypatch.setattr(
            "src.semantic_evals.get_circuit_breaker_state",
            MagicMock(return_value={"grok-test": {"open": True}}),
        )
        parse_mock = AsyncMock()
        monkeypatch.setattr("src.semantic_evals._parse_structured", parse_mock)

        await se._grade_and_record(_sample(), sstore)

        parse_mock.assert_not_called()
        assert get_semantic_eval_stats()["judge_failures"] == 1

    @pytest.mark.asyncio
    async def test_judge_never_writes_breaker_state(self, sstore, monkeypatch):
        """Observational-only contract: a judge run must not reset production
        failure counts or consume a half-open probe slot for the shared
        model — accumulated breaker state stays byte-identical."""
        import src.utils as U

        _patch_judge(monkeypatch, _verdict())
        await sstore.save_telemetry(
            "prompt", "API", 1, 1.0, 0.0, request_id="rid-semantic-test"
        )
        with U._BREAKER_LOCK:
            U._BREAKER_STATE["grok-test"] = {
                "consecutive_failures": 2, "opened_at": None, "trips": 1,
            }

        await se._grade_and_record(_sample(), sstore)

        assert get_semantic_eval_stats()["graded"] == 1  # the judge did run
        with U._BREAKER_LOCK:
            assert U._BREAKER_STATE["grok-test"] == {
                "consecutive_failures": 2, "opened_at": None, "trips": 1,
            }


# ─────────────────────────────────────────────────────────────────────────────
# attach_semantic_scores (store-level)
# ─────────────────────────────────────────────────────────────────────────────

class TestAttachSemanticScores:
    @pytest.mark.asyncio
    async def test_matches_row_by_request_id(self, sstore):
        await sstore.save_telemetry("a", "API", 1, 1.0, 0.0, request_id="rid-a")
        await sstore.save_telemetry("b", "API", 1, 1.0, 0.0, request_id="rid-b")
        ok = await sstore.attach_semantic_scores("rid-a", {"v": 1, "overall": 4.0})
        assert ok is True
        rows = await sstore.get_telemetry_stats()
        by_intent = {row["intent"]: json.loads(row["metadata"]) for row in rows}
        assert by_intent["a"]["semantic"] == {"v": 1, "overall": 4.0}
        assert "semantic" not in by_intent["b"]

    @pytest.mark.asyncio
    async def test_skips_history_compaction_rows(self, sstore):
        # Compaction shares the turn's ambient request id; the block must
        # land on the turn's own row, not the auxiliary one.
        await sstore.save_telemetry("do the thing", "API", 1, 1.0, 0.0, request_id="rid-shared")
        await sstore.save_telemetry("history-compaction", "API", 1, 0.2, 0.001, request_id="rid-shared")
        ok = await sstore.attach_semantic_scores("rid-shared", {"v": 1})
        assert ok is True
        rows = await sstore.get_telemetry_stats()
        by_intent = {row["intent"]: json.loads(row["metadata"]) for row in rows}
        assert "semantic" in by_intent["do the thing"]
        assert "semantic" not in by_intent["history-compaction"]

    @pytest.mark.asyncio
    async def test_miss_returns_false(self, sstore):
        assert await sstore.attach_semantic_scores("rid-none", {"v": 1}) is False
        assert await sstore.attach_semantic_scores("", {"v": 1}) is False

    @pytest.mark.asyncio
    async def test_oversized_block_drops_rationale_first(self, sstore):
        await sstore.save_telemetry("a", "API", 1, 1.0, 0.0, request_id="rid-a")
        block = {"v": 1, "rationale": "r" * 2000, "overall": 4.0}
        assert await sstore.attach_semantic_scores("rid-a", block) is True
        rows = await sstore.get_telemetry_stats()
        semantic = json.loads(rows[0]["metadata"])["semantic"]
        assert "rationale" not in semantic
        assert semantic["overall"] == 4.0


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end through run_agent_turn
# ─────────────────────────────────────────────────────────────────────────────

class TestRunAgentTurnTrigger:
    @pytest.mark.asyncio
    async def test_trigger_end_to_end(self, sstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS_RATE", "1.0")
        se.set_testing_override(True)
        monkeypatch.setattr("src.utils.store", sstore)
        monkeypatch.setattr(
            "src.utils.orchestrate",
            AsyncMock(return_value=MetaLayer(
                generation="answer", finish_reason="final_answer",
                model="grok-test", plane="API", route="coding",
            )),
        )
        _patch_judge(monkeypatch, _verdict())

        token = set_request_id("rid-e2e-semantic")
        try:
            # The turn's telemetry row (normally written by orchestrate)
            # carries the ambient request id.
            await sstore.save_telemetry("hi", "API", 1, 0.5, 0.01)
            layer = await run_agent_turn(prompt="hi")
        finally:
            reset_request_id(token)

        assert layer.generation == "answer"
        await se.wait_for_pending()
        rows = await sstore.get_telemetry_stats()
        meta = json.loads(rows[0]["metadata"])
        assert meta["request_id"] == "rid-e2e-semantic"
        assert meta["semantic"]["scores"]["correctness"] == 4
        assert get_semantic_eval_stats()["graded"] == 1

    @pytest.mark.asyncio
    async def test_trigger_never_breaks_the_turn(self, sstore, monkeypatch):
        """A crashing sampler must not affect the turn's result."""
        monkeypatch.setenv("UNIGROK_SEMANTIC_EVALS", "shadow")
        monkeypatch.setattr("src.utils.store", sstore)
        monkeypatch.setattr(
            "src.utils.orchestrate",
            AsyncMock(return_value=MetaLayer(generation="ok", finish_reason="final_answer")),
        )
        with patch(
            "src.semantic_evals.maybe_submit_semantic_eval",
            side_effect=RuntimeError("boom"),
        ):
            layer = await run_agent_turn(prompt="hi")
        assert layer.generation == "ok"
