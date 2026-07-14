# tests/test_swarm_storage.py
# Swarm optimizer storage (migration v13: v2 task contracts + lineage) and
# the UNIGROK_SWARM_* config ladder. C1 scope only — no engine, no tools.

import asyncio
import json

import pytest

from src.swarm.config import (
    reset_swarm_state,
    swarm_bench_repeats,
    swarm_child_mem_mb,
    swarm_default_budget_usd,
    swarm_eval_timeout,
    swarm_max_concurrent_gen,
    swarm_max_generations,
    swarm_mode,
    swarm_population,
    swarm_stale_after_sec,
    validate_primary_goal,
    validate_search_strategy,
)
from src.utils import GrokSessionStore


@pytest.fixture(autouse=True)
def _reset_swarm():
    reset_swarm_state()
    yield
    reset_swarm_state()


@pytest.fixture
async def wstore(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "swarm.db")
    yield s
    await s.close()


def _candidate(**overrides):
    defaults = dict(
        id="cand-1",
        task_id="task-1",
        generation=1,
        mutator="algorithmic",
        plane="CLI",
        byte_start=100,
        byte_end=400,
        code="def fast(x):\n    return sorted(x)\n",
        code_hash="hash-1",
        stage_reached="bench",
        feasible=True,
        latency_ms=12.5,
        peak_mem_bytes=1024,
        diff_bytes=42,
        reward=1.0,
        arm_receipt=json.dumps({"arm": "algorithmic", "seed_step": 3}),
    )
    defaults.update(overrides)
    return defaults


async def _create_task(store, task_id="task-1", **overrides):
    defaults = dict(
        target_path="src/slow.py",
        focus_node="function:slow_sort",
        base_file_hash="abc123",
        test_target="tests/test_slow.py",
        bench_command="python scripts/bench_slow.py",
        budget_usd=2.0,
        seed=42,
    )
    defaults.update(overrides)
    await store.create_swarm_task(task_id, **defaults)


# ─────────────────────────────────────────────────────────────────────────────
# Migration head
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmMigration:
    @pytest.mark.asyncio
    async def test_fresh_db_reaches_head_and_reopen_is_idempotent(self, tmp_path):
        db_path = tmp_path / "migrate.db"
        s = GrokSessionStore(db_path=db_path)
        await s._ensure_initialized()
        async with s._read_conn() as conn:
            async with conn.execute("PRAGMA user_version;") as cursor:
                version = (await cursor.fetchone())[0]
        assert version == 16
        await s.close()

        # Reopen: migration gates must all no-op and the tables survive.
        s2 = GrokSessionStore(db_path=db_path)
        await _create_task(s2, "task-reopen")
        assert (await s2.get_swarm_task("task-reopen"))["status"] == "queued"
        async with s2._read_conn() as conn:
            async with conn.execute("PRAGMA user_version;") as cursor:
                assert (await cursor.fetchone())[0] == 16
        await s2.close()


# ─────────────────────────────────────────────────────────────────────────────
# swarm_tasks CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmTasks:
    @pytest.mark.asyncio
    async def test_create_and_get_round_trip(self, wstore):
        await _create_task(wstore)
        row = await wstore.get_swarm_task("task-1")
        assert row["target_path"] == "src/slow.py"
        assert row["focus_node"] == "function:slow_sort"
        assert row["status"] == "queued"
        assert row["budget_usd"] == pytest.approx(2.0)
        assert row["spent_usd"] == pytest.approx(0.0)
        assert row["generation"] == 0
        assert row["seed"] == 42
        assert row["search_strategy"] == "baseline_batch"
        assert row["primary_goal"] == "balanced"
        assert row["input_kind"] == "workspace"
        assert row["analytics_json"] is None
        assert row["champion_id"] is None
        assert row["created_at"] and row["updated_at"]

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, wstore):
        assert await wstore.get_swarm_task("nope") is None

    @pytest.mark.asyncio
    async def test_update_bumps_heartbeat_even_with_no_fields(self, wstore):
        """updated_at always bumps — it IS the runner's heartbeat."""
        await _create_task(wstore)
        before = (await wstore.get_swarm_task("task-1"))["updated_at"]
        await asyncio.sleep(0.02)
        await wstore.update_swarm_task("task-1")
        after = (await wstore.get_swarm_task("task-1"))["updated_at"]
        assert after > before

    @pytest.mark.asyncio
    async def test_update_fields_round_trip(self, wstore):
        await _create_task(wstore)
        await wstore.update_swarm_task(
            "task-1",
            status="running",
            spent_usd=0.5,
            generation=3,
            baseline_json=json.dumps({"latency_ms": 100.0}),
            oracle_json=json.dumps({"focus_coverage_pct": 87.5}),
            folded_state="[Compacted state fold of 0 earlier messages]",
            analytics_json=json.dumps({"format": "unigrok-swarm-analytics-v1"}),
            champion_id="cand-1",
        )
        row = await wstore.get_swarm_task("task-1")
        assert row["status"] == "running"
        assert row["spent_usd"] == pytest.approx(0.5)
        assert row["generation"] == 3
        assert json.loads(row["baseline_json"])["latency_ms"] == 100.0
        assert json.loads(row["oracle_json"])["focus_coverage_pct"] == 87.5
        assert row["folded_state"].startswith("[Compacted")
        assert json.loads(row["analytics_json"])["format"] == "unigrok-swarm-analytics-v1"
        assert row["champion_id"] == "cand-1"

    @pytest.mark.asyncio
    async def test_list_orders_newest_first(self, wstore):
        await _create_task(wstore, "task-a")
        await asyncio.sleep(0.02)
        await _create_task(wstore, "task-b")
        rows = await wstore.list_swarm_tasks()
        assert [r["id"] for r in rows] == ["task-b", "task-a"]

    @pytest.mark.asyncio
    async def test_caller_falls_back_to_ambient_context(self, wstore):
        from src.utils import reset_active_caller, set_active_caller

        token = set_active_caller("swarm-tester")
        try:
            await _create_task(wstore, "task-attr")
        finally:
            reset_active_caller(token)
        assert (await wstore.get_swarm_task("task-attr"))["caller"] == "swarm-tester"


# ─────────────────────────────────────────────────────────────────────────────
# swarm_candidates
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmCandidates:
    @pytest.mark.asyncio
    async def test_insert_and_list_round_trip(self, wstore):
        await _create_task(wstore)
        assert await wstore.insert_swarm_candidate(_candidate()) is True
        rows = await wstore.list_swarm_candidates("task-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["mutator"] == "algorithmic"
        assert row["feasible"] == 1
        assert row["byte_start"] == 100 and row["byte_end"] == 400
        assert row["latency_ms"] == pytest.approx(12.5)
        assert row["parent_id"] is None  # v2 elite-offspring reservation
        assert row["parent_code_hash"] is None
        assert row["origin"] == "llm"
        assert row["transform"] is None
        assert json.loads(row["arm_receipt"])["arm"] == "algorithmic"

    @pytest.mark.asyncio
    async def test_duplicate_code_hash_returns_false(self, wstore):
        await _create_task(wstore)
        assert await wstore.insert_swarm_candidate(_candidate()) is True
        dup = _candidate(id="cand-2")  # same task_id + code_hash
        assert await wstore.insert_swarm_candidate(dup) is False
        assert len(await wstore.list_swarm_candidates("task-1")) == 1
        # Same hash under a DIFFERENT task is fine.
        await _create_task(wstore, "task-2")
        assert await wstore.insert_swarm_candidate(
            _candidate(id="cand-3", task_id="task-2")
        ) is True

    @pytest.mark.asyncio
    async def test_list_filters_feasible_and_generation(self, wstore):
        await _create_task(wstore)
        await wstore.insert_swarm_candidate(_candidate())
        await wstore.insert_swarm_candidate(
            _candidate(id="cand-2", code_hash="hash-2", feasible=False,
                       stage_reached="tests", generation=2)
        )
        assert len(await wstore.list_swarm_candidates("task-1", feasible_only=True)) == 1
        gen2 = await wstore.list_swarm_candidates("task-1", generation=2)
        assert [r["id"] for r in gen2] == ["cand-2"]

    @pytest.mark.asyncio
    async def test_oversized_code_is_rejected_not_truncated(self, wstore):
        """The stored code is what apply splices — silent truncation would
        corrupt the file, so oversized candidates are refused outright."""
        await _create_task(wstore)
        with pytest.raises(ValueError, match="64KB"):
            await wstore.insert_swarm_candidate(_candidate(code="x" * 70000))

    @pytest.mark.asyncio
    async def test_secret_bearing_code_is_rejected(self, wstore):
        await _create_task(wstore)
        leaky = _candidate(code='KEY = "xai-abcdefgh12345678"\n')
        with pytest.raises(ValueError, match="secret"):
            await wstore.insert_swarm_candidate(leaky)

    @pytest.mark.asyncio
    async def test_empty_code_is_rejected(self, wstore):
        await _create_task(wstore)
        with pytest.raises(ValueError, match="non-empty"):
            await wstore.insert_swarm_candidate(_candidate(code=""))


# ─────────────────────────────────────────────────────────────────────────────
# Config ladder
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmConfig:
    def test_strategy_and_goal_defaults_and_validation(self):
        assert validate_search_strategy(None) == "baseline_batch"
        assert validate_search_strategy(" ELITE_OFFSPRING ") == "elite_offspring"
        assert validate_primary_goal(None) == "balanced"
        assert validate_primary_goal("LATENCY") == "latency"
        with pytest.raises(ValueError, match="search_strategy"):
            validate_search_strategy("evolve")
        with pytest.raises(ValueError, match="primary_goal"):
            validate_primary_goal("fastest")

    def test_mode_defaults_off(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_SWARM", raising=False)
        assert swarm_mode() == "off"

    def test_valid_modes(self, monkeypatch):
        for mode in ("off", "dry_run", "active"):
            monkeypatch.setenv("UNIGROK_SWARM", mode)
            assert swarm_mode() == mode

    def test_unknown_mode_warns_once_and_reads_off(self, monkeypatch, caplog):
        monkeypatch.setenv("UNIGROK_SWARM", "bogus")
        with caplog.at_level("WARNING", logger="GrokMCP"):
            assert swarm_mode() == "off"
            assert swarm_mode() == "off"
        warnings = [r for r in caplog.records if "UNIGROK_SWARM" in r.message]
        assert len(warnings) == 1

    def test_defaults_match_plan(self, monkeypatch):
        for var in (
            "UNIGROK_SWARM_MAX_GENERATIONS", "UNIGROK_SWARM_POPULATION",
            "UNIGROK_SWARM_MAX_CONCURRENT_GEN", "UNIGROK_SWARM_EVAL_TIMEOUT",
            "UNIGROK_SWARM_BENCH_REPEATS", "UNIGROK_SWARM_DEFAULT_BUDGET_USD",
            "UNIGROK_SWARM_CHILD_MEM_MB",
        ):
            monkeypatch.delenv(var, raising=False)
        assert swarm_max_generations() == 6
        assert swarm_population() == 4
        assert swarm_max_concurrent_gen() == 2
        assert swarm_eval_timeout() == pytest.approx(120.0)
        assert swarm_bench_repeats() == 5
        assert swarm_default_budget_usd() == pytest.approx(2.00)
        assert swarm_child_mem_mb() == 2048

    def test_values_clamp_to_ceilings(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "999")
        monkeypatch.setenv("UNIGROK_SWARM_POPULATION", "0")
        monkeypatch.setenv("UNIGROK_SWARM_EVAL_TIMEOUT", "not-a-number")
        assert swarm_max_generations() == 20
        assert swarm_population() == 1
        assert swarm_eval_timeout() == pytest.approx(120.0)

    def test_stale_horizon_derives_from_eval_timeout(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM_EVAL_TIMEOUT", "100")
        assert swarm_stale_after_sec() == pytest.approx(300.0)
