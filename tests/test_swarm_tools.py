# tests/test_swarm_tools.py
# MCP tool surface: triple gates (mode / contributor / workspace / cloudrun),
# path-traversal refusal, and a full start -> status -> apply drive through the
# REAL runner against the golden dedup target with a fake generator. Apply
# safety (staleness guard, post-apply re-verify + restore) is the crux.

import shutil
from pathlib import Path

import pytest

import src.tools.swarm as swarm_tools
from src.swarm.generate import GenerationResult
from src.utils import GrokSessionStore

GOLDEN = Path(__file__).parent.parent / "evals" / "tasks" / "swarm_targets" / "nsquared_dedup"

_FAST_DEDUP = (
    "def dedup(items):\n"
    "    seen = set()\n"
    "    result = []\n"
    "    for item in items:\n"
    "        if item not in seen:\n"
    "            seen.add(item)\n"
    "            result.append(item)\n"
    "    return result"
)
_WRONG_DEDUP = "def dedup(items):\n    return list(items)"  # keeps duplicates → tests fail


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


class TestGates:
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
