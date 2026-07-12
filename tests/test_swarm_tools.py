# tests/test_swarm_tools.py
# MCP tool surface: triple gates (mode / contributor / workspace / cloudrun),
# path-traversal refusal, and a full start -> status -> apply drive through the
# REAL runner against the golden dedup target with a fake generator. Apply
# safety (staleness guard, post-apply re-verify + restore) is the crux.

import shutil
from pathlib import Path

import pytest

import src.tools.swarm as swarm_tools
from src.swarm.generate import BudgetExceeded, GenerationResult
from src.utils import GrokSessionStore

GOLDEN = Path(__file__).parent.parent / "evals" / "tasks" / "swarm_targets" / "nsquared_dedup"
GOLDEN_ROOT = GOLDEN.parent

_FAST_DEDUP = (
    "def dedup(items):\n"
    "    seen_hashable = set()\n"
    "    seen_unhashable = []\n"
    "    result = []\n"
    "    for item in items:\n"
    "        try:\n"
    "            if item in seen_hashable or item in seen_unhashable:\n"
    "                continue\n"
    "            seen_hashable.add(item)\n"
    "        except TypeError:\n"
    "            if item in result:\n"
    "                continue\n"
    "            seen_unhashable.append(item)\n"
    "        result.append(item)\n"
    "    return result"
)
_WRONG_DEDUP = "def dedup(items):\n    return list(items)"  # keeps duplicates → tests fail

_GOLDEN_CASES = (
    {
        "name": "nsquared_dedup",
        "target": "dedup.py",
        "focus_node": "function:dedup",
        "test": "test_dedup.py",
        "bench": "bench_dedup.py",
        "optimized": _FAST_DEDUP,
    },
    {
        "name": "slow_loop_optimize",
        "target": "loop_opt.py",
        "focus_node": "function:slow_accumulate",
        "test": "test_loop_opt.py",
        "bench": "bench_loop_opt.py",
        "optimized": (
            "def slow_accumulate(records):\n"
            "    return \"\".join(record + \"\\n\" for record in records)"
        ),
    },
)


@pytest.fixture(params=_GOLDEN_CASES, ids=lambda case: case["name"])
def golden_target(request, tmp_path):
    case = request.param
    workspace = tmp_path / "golden-workspace"
    package = workspace / "pkg"
    package.mkdir(parents=True)
    source = GOLDEN_ROOT / case["name"]
    for path in source.iterdir():
        if path.is_file():
            shutil.copyfile(path, package / path.name)
    return case, workspace


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    for name in ("dedup.py", "test_dedup.py", "bench_dedup.py"):
        shutil.copyfile(GOLDEN / name, ws / "pkg" / name)
    return ws


@pytest.fixture
async def wired(workspace, tmp_path, monkeypatch):
    store = GrokSessionStore(db_path=tmp_path / "swarm_tools.db")
    monkeypatch.setattr(swarm_tools, "store", store)
    monkeypatch.setattr(swarm_tools.PathResolver, "contributor_mode", staticmethod(lambda: True))
    monkeypatch.setattr(swarm_tools.PathResolver, "get_workspace_root", classmethod(lambda cls: workspace))
    monkeypatch.setattr(swarm_tools.PathResolver, "get_state_base_dir", classmethod(lambda cls: tmp_path / "state"))
    monkeypatch.setattr(swarm_tools, "is_cloudrun_runtime", lambda: False)
    # Fresh runner bound to the patched store.
    swarm_tools._RUNNER = None

    async def fake_gen(prompt, system, *, remaining_budget_usd, **kw):
        text = _FAST_DEDUP if "faster one" in prompt else _WRONG_DEDUP
        return GenerationResult(text, "CLI", 0.0, "final_answer")

    # The runner builds engines with generator=None, which resolves to the
    # module-level generate_mutation at call time — patch that.
    monkeypatch.setattr("src.swarm.engine.generate_mutation", fake_gen)
    yield store, workspace
    await store.close()


class TestGoldenTargets:
    @pytest.mark.asyncio
    async def test_focus_oracle_and_benchmark_contract(
        self, golden_target, tmp_path, monkeypatch
    ):
        """Every registered target reaches the real preflight and funnel."""
        import json as jsonlib

        case, workspace = golden_target
        store = GrokSessionStore(db_path=tmp_path / f"{case['name']}.db")
        monkeypatch.setattr(swarm_tools, "store", store)
        monkeypatch.setattr(
            swarm_tools.PathResolver, "contributor_mode", staticmethod(lambda: True)
        )
        monkeypatch.setattr(
            swarm_tools.PathResolver,
            "get_workspace_root",
            classmethod(lambda cls: workspace),
        )
        monkeypatch.setattr(
            swarm_tools.PathResolver,
            "get_state_base_dir",
            classmethod(lambda cls: tmp_path / f"state-{case['name']}"),
        )
        monkeypatch.setattr(swarm_tools, "is_cloudrun_runtime", lambda: False)
        swarm_tools._RUNNER = None

        async def fake_gen(prompt, system, *, remaining_budget_usd, **kwargs):
            return GenerationResult(case["optimized"], "CLI", 0.0, "final_answer")

        monkeypatch.setattr("src.swarm.engine.generate_mutation", fake_gen)
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "1")
        monkeypatch.setenv("UNIGROK_SWARM_POPULATION", "1")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")
        try:
            out = await swarm_tools.start_code_swarm(
                f"pkg/{case['target']}",
                case["focus_node"],
                f"pkg/{case['test']}",
                f"python pkg/{case['bench']}",
                allow_unstable_bench=True,
            )
            task_id = out.split("`")[1]
            completed = await swarm_tools._get_runner().wait(task_id, timeout=60.0)
            assert completed is True
            payload = jsonlib.loads(
                await swarm_tools.get_swarm_status(task_id, view="json")
            )
            assert payload["format"] == "unigrok-swarm-status-v2"
            assert payload["status"] == "completed"
            assert payload["target"]["focus_node"] == case["focus_node"]
            assert payload["oracle"]["focus_coverage_pct"] > 0
            assert payload["baseline"]["latency_ms"] > 0
            assert payload["aggregates"]["candidates_total"] >= 1
        finally:
            await store.close()
            swarm_tools._RUNNER = None


class TestGates:
    @pytest.mark.asyncio
    async def test_unknown_strategy_and_goal_are_rejected(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        bad_strategy = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py",
            "python pkg/bench_dedup.py", search_strategy="evolve",
        )
        assert "unknown search_strategy" in bad_strategy
        bad_goal = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py",
            "python pkg/bench_dedup.py", primary_goal="fastest",
        )
        assert "unknown primary_goal" in bad_goal

    @pytest.mark.asyncio
    async def test_off_mode_refuses(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "off")
        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py", "python pkg/bench_dedup.py"
        )
        assert "off" in out.lower()

    @pytest.mark.asyncio
    async def test_stable_mode_refuses(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        monkeypatch.setattr(swarm_tools.PathResolver, "contributor_mode", staticmethod(lambda: False))
        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py", "python pkg/bench_dedup.py"
        )
        assert "contributor" in out.lower()

    @pytest.mark.asyncio
    async def test_path_traversal_refused(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        out = await swarm_tools.start_code_swarm(
            "../../etc/passwd.py", "function:x", "pkg/test_dedup.py", "python pkg/bench_dedup.py"
        )
        assert "escape" in out.lower() or "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_non_python_target_refused(self, wired, workspace, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        (workspace / "pkg" / "notes.txt").write_text("hi")
        out = await swarm_tools.start_code_swarm(
            "pkg/notes.txt", "function:x", "pkg/test_dedup.py", "python pkg/bench_dedup.py"
        )
        assert "python" in out.lower()

    @pytest.mark.asyncio
    async def test_test_target_traversal_refused(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "../../outside.py",
            "python pkg/bench_dedup.py",
        )
        assert "test_target" in out and "workspace" in out

    @pytest.mark.asyncio
    async def test_arbitrary_benchmark_executable_refused(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py",
            "/bin/sh -c 'echo SWARM_BENCH'",
        )
        assert "bench_command" in out and "python" in out


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_cli_cost_contract_violation_is_a_failed_task(self, wired, monkeypatch):
        import json as jsonlib

        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")

        async def charged_gen(prompt, system, *, remaining_budget_usd, **kwargs):
            raise BudgetExceeded("API result cost $1.00")

        monkeypatch.setattr("src.swarm.engine.generate_mutation", charged_gen)
        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py",
            "function:dedup",
            "pkg/test_dedup.py",
            "python pkg/bench_dedup.py",
            allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        assert await swarm_tools._get_runner().wait(task_id, timeout=60.0) is True
        payload = jsonlib.loads(
            await swarm_tools.get_swarm_status(task_id, view="json")
        )
        assert payload["status"] == "failed"
        assert "CLI-only zero-cost contract" in payload["oracle"]["error"]
        assert payload["aggregates"]["cost_to_optimize_usd"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_paste_to_verified_champion_is_copy_only(self, wired, monkeypatch):
        import json as jsonlib

        store, _workspace = wired
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "2")
        monkeypatch.setenv("UNIGROK_SWARM_POPULATION", "4")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")
        source = (GOLDEN / "dedup.py").read_text()
        test_code = '''
from module_under_test import dedup

def test_dedup():
    assert dedup([1, 2, 1, 3, 2]) == [1, 2, 3]
'''.lstrip()
        bench_code = '''
import json
from module_under_test import dedup
dedup([1, 2, 1])
print("SWARM_BENCH " + json.dumps({"latency_ms": 5.0, "peak_mem_bytes": 2048}))
'''.lstrip()

        out = await swarm_tools.start_paste_swarm(
            source, test_code, bench_code, "function:dedup",
            search_strategy="elite_offspring", allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        await swarm_tools._get_runner().wait(task_id, timeout=60.0)
        task = await store.get_swarm_task(task_id)
        assert task["input_kind"] == "paste"
        assert task["search_strategy"] == "elite_offspring"
        assert task["target_path"].startswith("paste://")

        payload = jsonlib.loads(await swarm_tools.get_swarm_status(task_id, view="json"))
        assert payload["status"] == "completed"
        assert payload["input_kind"] == "paste"
        assert payload["champion_id"] in payload["pareto_front"]
        champion = next(
            candidate
            for generation in payload["generations"]
            for candidate in generation["candidates"]
            if candidate["candidate_id"] == payload["champion_id"]
        )
        assert champion["code"]
        monkeypatch.setenv("UNIGROK_SWARM", "active")
        apply_out = await swarm_tools.apply_swarm_winner(payload["champion_id"])
        assert "copy-only" in apply_out

    @pytest.mark.asyncio
    async def test_paste_requires_oracle_and_rejects_secrets(self, wired, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        missing = await swarm_tools.start_paste_swarm(
            "def f(x):\n    return x\n", "", "", "function:f"
        )
        assert "test_code is required" in missing
        secret = await swarm_tools.start_paste_swarm(
            'KEY = "xai-abcdefgh12345678"\ndef f(x):\n    return x\n',
            "def test_f(): pass\n",
            'print("SWARM_BENCH {}")\n',
            "function:f",
        )
        assert "secret-like" in secret

    @pytest.mark.asyncio
    async def test_dry_run_finds_front_and_refuses_apply(self, wired, monkeypatch):
        store, _ws = wired
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "2")
        monkeypatch.setenv("UNIGROK_SWARM_POPULATION", "4")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")

        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py", "python pkg/bench_dedup.py",
            allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        await swarm_tools._get_runner().wait(task_id, timeout=60.0)

        status = await swarm_tools.get_swarm_status(task_id)
        assert "coverage" in status.lower()
        assert "Pareto front" in status
        # A feasible candidate exists; apply is refused in dry_run.
        candidates = await store.list_swarm_candidates(task_id, feasible_only=True)
        assert candidates, "the fast dedup should be feasible"
        apply_out = await swarm_tools.apply_swarm_winner(candidates[0]["id"])
        assert "dry_run" in apply_out or "disabled" in apply_out

    @pytest.mark.asyncio
    async def test_json_view_carries_the_full_replayable_run(self, wired, monkeypatch):
        """unigrok-swarm-status-v2: one payload renders the whole run —
        generations, outcomes for color mapping, front ids, honest aggregates
        — and carries ONLY measured fields (no hardware counters, no
        semantic scores, no invented cost comparisons)."""
        import json as jsonlib

        store, _ws = wired
        monkeypatch.setenv("UNIGROK_SWARM", "dry_run")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "2")
        monkeypatch.setenv("UNIGROK_SWARM_POPULATION", "4")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")

        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py", "python pkg/bench_dedup.py",
            allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        await swarm_tools._get_runner().wait(task_id, timeout=60.0)

        payload = jsonlib.loads(await swarm_tools.get_swarm_status(task_id, view="json"))
        assert payload["format"] == "unigrok-swarm-status-v2"
        assert payload["task_id"] == task_id
        assert payload["mode"] == "dry_run"
        assert payload["input_kind"] == "workspace"
        assert payload["search_strategy"] == "baseline_batch"
        assert payload["primary_goal"] == "balanced"
        assert payload["analytics"]["format"] == "unigrok-swarm-analytics-v1"
        assert payload["target"]["focus_node"] == "function:dedup"
        assert payload["oracle"]["focus_coverage_pct"] > 0
        assert payload["baseline"]["latency_ms"] > 0
        assert [g["generation"] for g in payload["generations"]] == [1, 2]

        candidates = [c for g in payload["generations"] for c in g["candidates"]]
        assert candidates
        assert {c["outcome"] for c in candidates} <= {
            "pareto_elite", "dominated", "static_wall", "test_wall"
        }
        front_ids = set(payload["pareto_front"])
        assert front_ids  # the fast dedup wins
        assert payload["champion_id"] in front_ids
        assert payload["comparison"]["champion"]["candidate_id"] == payload["champion_id"]
        assert payload["comparison"]["diff_from_original"]
        for c in candidates:
            if c["candidate_id"] in front_ids:
                assert c["outcome"] == "pareto_elite"
                assert c.get("code")  # elites carry code for the diff view
            else:
                assert "code" not in c  # bounded payload: non-elites don't
            assert c["origin"] == "llm"
            assert "parent_id" in c

        agg = payload["aggregates"]
        assert agg["candidates_total"] == len(candidates)
        assert 0 < agg["feasibility_rate"] <= 1
        assert agg["cost_to_optimize_usd"] == pytest.approx(0.0)  # CLI plane
        # Honest omissions: unmeasured fields must not exist at all.
        for c in candidates:
            for absent in ("instructions_retired", "allocated_blocks",
                           "semantic_correctness", "semantic_safety"):
                assert absent not in c
        assert "kv_cache_savings_pct" not in agg

        # File untouched (dry_run) → live span present and not stale.
        assert payload["original_span_stale"] is False
        assert "def dedup" in payload["original_span"]

        # The text view already redacts oracle failures; the JSON view must
        # not reopen that secret-bearing output channel.
        await store.update_swarm_task(
            task_id,
            oracle_json=jsonlib.dumps(
                {"error": "failed with XAI_API_KEY=xai-supersecret123456"}
            ),
        )
        redacted = jsonlib.loads(
            await swarm_tools.get_swarm_status(task_id, view="json")
        )
        assert "xai-supersecret123456" not in redacted["oracle"]["error"]

    @pytest.mark.asyncio
    async def test_json_view_unknown_task(self, wired):
        import json as jsonlib

        payload = jsonlib.loads(await swarm_tools.get_swarm_status("nope", view="json"))
        assert "error" in payload

    @pytest.mark.asyncio
    async def test_list_swarm_tasks_newest_first_with_effective_status(self, wired):
        """The Playground's task picker: JSON array, newest first, staleness
        override applied, empty array on a fresh gateway."""
        import asyncio as aio
        import json as jsonlib

        store, _ws = wired
        assert jsonlib.loads(await swarm_tools.list_swarm_tasks()) == []

        await store.create_swarm_task(
            "task-old", target_path="a.py", focus_node="function:f",
            base_file_hash="h", test_target="t", bench_command="b",
            budget_usd=1.0, seed=1,
        )
        await aio.sleep(0.02)
        await store.create_swarm_task(
            "task-new", target_path="b.py", focus_node="function:g",
            base_file_hash="h", test_target="t", bench_command="b",
            budget_usd=1.0, seed=2,
        )
        await store.update_swarm_task("task-new", status="completed", generation=3)

        items = jsonlib.loads(await swarm_tools.list_swarm_tasks())
        assert [i["task_id"] for i in items] == ["task-new", "task-old"]
        newest = items[0]
        assert newest["status"] == "completed"
        assert newest["focus_node"] == "function:g"
        assert newest["generations"] == 3
        assert newest["spent_usd"] == pytest.approx(0.0)
        # A queued row with a fresh heartbeat reads as queued, not stale.
        assert items[1]["status"] == "queued"

    @pytest.mark.asyncio
    async def test_active_apply_lands_and_reverifies(self, wired, monkeypatch):
        store, workspace = wired
        monkeypatch.setenv("UNIGROK_SWARM", "active")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "2")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")

        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py", "python pkg/bench_dedup.py",
            allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        await swarm_tools._get_runner().wait(task_id, timeout=60.0)
        candidates = await store.list_swarm_candidates(task_id, feasible_only=True)
        assert candidates
        winner = candidates[0]

        # A current front is provisional while later candidates can still
        # arrive. Applying it mid-run would mutate the workspace underneath
        # the active search.
        monkeypatch.setattr(swarm_tools, "effective_status", lambda _task: "running")
        early = await swarm_tools.apply_swarm_winner(winner["id"])
        assert "still running" in early
        monkeypatch.setattr(swarm_tools, "effective_status", lambda _task: "completed")

        before = (workspace / "pkg" / "dedup.py").read_text()
        apply_out = await swarm_tools.apply_swarm_winner(winner["id"])
        assert "Applied" in apply_out and "re-verified" in apply_out
        after = (workspace / "pkg" / "dedup.py").read_text()
        assert after != before
        assert winner["code"].strip() in after  # the winning slice landed

    @pytest.mark.asyncio
    async def test_apply_refuses_non_front_candidate(self, wired, monkeypatch):
        store, _workspace = wired
        monkeypatch.setenv("UNIGROK_SWARM", "active")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "1")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")

        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py",
            "python pkg/bench_dedup.py", allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        await swarm_tools._get_runner().wait(task_id, timeout=60.0)
        candidates = await store.list_swarm_candidates(task_id)
        loser = next(candidate for candidate in candidates if not candidate["feasible"])
        apply_out = await swarm_tools.apply_swarm_winner(loser["id"])
        assert "not on the current verified Pareto front" in apply_out

    @pytest.mark.asyncio
    async def test_apply_refused_when_file_changed(self, wired, monkeypatch):
        store, workspace = wired
        monkeypatch.setenv("UNIGROK_SWARM", "active")
        monkeypatch.setenv("UNIGROK_SWARM_MAX_GENERATIONS", "1")
        monkeypatch.setenv("UNIGROK_SWARM_BENCH_REPEATS", "3")

        out = await swarm_tools.start_code_swarm(
            "pkg/dedup.py", "function:dedup", "pkg/test_dedup.py", "python pkg/bench_dedup.py",
            allow_unstable_bench=True,
        )
        task_id = out.split("`")[1]
        await swarm_tools._get_runner().wait(task_id, timeout=60.0)
        candidates = await store.list_swarm_candidates(task_id, feasible_only=True)
        assert candidates

        # Mutate the live file so its hash no longer matches base_file_hash.
        target = workspace / "pkg" / "dedup.py"
        target.write_text(target.read_text() + "\n# edited\n")
        apply_out = await swarm_tools.apply_swarm_winner(candidates[0]["id"])
        assert "changed since the swarm ran" in apply_out
