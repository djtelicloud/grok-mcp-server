# tests/test_swarm_sandbox.py
# Sandbox (copy, venv/PYTHONPATH shadowing, child isolation, hygiene, restore)
# and preflight (provenance, baseline budget, focus coverage, bench stability)
# driven against the tests/fixtures/swarm_target mini-project via real
# subprocesses. These are the C2 ship-blockers Grok flagged.

import shutil
import sys
from pathlib import Path

import pytest

from src.swarm.ast_utils import extract_node_span, span_line_range
from src.swarm.preflight import PreflightError, noise_floor_pct, run_preflight
from src.swarm.sandbox import SandboxError, SwarmSandbox, parse_bench_line

FIXTURE = Path(__file__).parent / "fixtures" / "swarm_target"


@pytest.fixture
def workspace(tmp_path):
    """A throwaway workspace = the fixture project copied to a temp dir."""
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE, ws)
    # A stray state file from a previous unstable-bench run must never leak in.
    (ws / ".bench_state").unlink(missing_ok=True)
    return ws


@pytest.fixture
def sandbox(workspace, tmp_path):
    sb = SwarmSandbox(workspace, tmp_path / "wr", "slow_mod.py")
    sb.create()
    yield sb
    sb.destroy()


def _span_lines(sb):
    src = sb.read_target()
    start, end = extract_node_span(src, "function:slow_sort")
    return span_line_range(src, start, end)


async def _preflight(sb, *, test_target="test_slow.py", bench="bench_slow.py",
                     repeats=3, stage_fraction=0.9, allow_unstable=False):
    return await run_preflight(
        sb,
        target_rel="slow_mod.py",
        span_lines=_span_lines(sb),
        test_target=test_target,
        bench_argv=[sb.python_bin(), bench],
        bench_repeats=repeats,
        eval_timeout=60.0,
        stage_budget_fraction=stage_fraction,
        allow_unstable_bench=allow_unstable,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bench contract parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestParseBenchLine:
    def test_valid_line_with_log_noise(self):
        out = 'starting\nSWARM_BENCH {"latency_ms": 12.5, "peak_mem_bytes": 2048}\ndone\n'
        assert parse_bench_line(out) == {"latency_ms": 12.5, "peak_mem_bytes": 2048.0}

    def test_missing_line_is_none(self):
        assert parse_bench_line("no marker here") is None

    def test_duplicate_lines_is_none(self):
        out = 'SWARM_BENCH {"latency_ms":1,"peak_mem_bytes":1}\nSWARM_BENCH {"latency_ms":2,"peak_mem_bytes":2}'
        assert parse_bench_line(out) is None

    def test_malformed_json_is_none(self):
        assert parse_bench_line("SWARM_BENCH not-json") is None

    def test_negative_values_rejected(self):
        assert parse_bench_line('SWARM_BENCH {"latency_ms":-1,"peak_mem_bytes":10}') is None


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox mechanics
# ─────────────────────────────────────────────────────────────────────────────

class TestSandbox:
    def test_copy_excludes_git_and_caches(self, workspace, tmp_path):
        (workspace / ".git").mkdir()
        (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main")
        # The source fixture may already contain an ignored cache from a
        # contributor's explicit fixture run; the copy contract is the same.
        (workspace / "__pycache__").mkdir(exist_ok=True)
        (workspace / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        sb = SwarmSandbox(workspace, tmp_path / "wr", "slow_mod.py")
        sb.create()
        assert (sb.work / "slow_mod.py").exists()
        assert not (sb.work / ".git").exists()
        assert not (sb.work / "__pycache__").exists()
        sb.destroy()

    def test_copy_is_byte_exact(self, sandbox):
        assert sandbox.read_target() == (FIXTURE / "slow_mod.py").read_bytes()

    def test_copy_size_guard_refuses_oversized(self, workspace, tmp_path):
        (workspace / "big.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))
        sb = SwarmSandbox(workspace, tmp_path / "wr", "slow_mod.py", max_copy_mb=1)
        with pytest.raises(SandboxError, match="copy guard"):
            sb.create()

    def test_missing_target_refused(self, workspace, tmp_path):
        sb = SwarmSandbox(workspace, tmp_path / "wr", "does_not_exist.py")
        with pytest.raises(SandboxError, match="missing"):
            sb.create()

    def test_child_env_scrubs_secrets(self, sandbox, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "xai-secret")
        monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "mgmt-secret")
        env = sandbox.child_env()
        assert "XAI_API_KEY" not in env
        assert "XAI_MANAGEMENT_API_KEY" not in env
        assert env["PYTHONPATH"].startswith(str(sandbox.work))
        assert env["PYTHONHASHSEED"] == "0"

    def test_incompatible_workspace_venv_falls_back_to_runtime_python(
        self, workspace, tmp_path
    ):
        fake_python = workspace / ".venv" / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        fake_python.write_bytes(b"not-an-executable-for-this-platform")
        fake_python.chmod(0o755)
        sb = SwarmSandbox(workspace, tmp_path / "wr", "slow_mod.py")
        sb.create()
        assert sb.python_bin() == sys.executable
        sb.destroy()

    @pytest.mark.asyncio
    async def test_write_and_restore_target(self, sandbox):
        original = sandbox.read_target()
        sandbox.write_target(b"def slow_sort(x):\n    return x\n")
        assert sandbox.read_target() != original
        sandbox.write_target(original)
        assert sandbox.read_target() == original

    @pytest.mark.asyncio
    async def test_run_tests_discriminates_good_and_broken(self, sandbox):
        src = sandbox.read_target()
        start, end = extract_node_span(src, "function:slow_sort")
        good = src[:start] + b"def slow_sort(items):\n    return sorted(list(items))\n" + src[end:]
        sandbox.write_target(good)
        passed, _ = await sandbox.run_tests("test_slow.py", timeout=30.0)
        assert passed is True
        broken = src[:start] + b"def slow_sort(items):\n    return None\n" + src[end:]
        sandbox.write_target(broken)
        passed, _ = await sandbox.run_tests("test_slow.py", timeout=30.0)
        assert passed is False

    @pytest.mark.asyncio
    async def test_timeout_kills_runaway_child(self, sandbox):
        rc, _out, err = await sandbox.run_child(
            [sandbox.python_bin(), "-c", "import time; time.sleep(30)"], timeout=1.0
        )
        assert rc == -9
        assert "timeout" in err

    @pytest.mark.asyncio
    async def test_bench_returns_median_and_samples(self, sandbox):
        result = await sandbox.run_bench(
            [sandbox.python_bin(), "bench_slow.py"], repeats=3, timeout=30.0
        )
        assert result["latency_ms"] == pytest.approx(5.0)
        assert result["peak_mem_bytes"] == 2048
        assert len(result["latency_samples"]) == 3  # warmup discarded

    @pytest.mark.asyncio
    async def test_bench_contract_violation_raises(self, sandbox):
        with pytest.raises(SandboxError, match="SWARM_BENCH"):
            await sandbox.run_bench(
                [sandbox.python_bin(), "-c", "print('no marker')"], repeats=2, timeout=15.0
            )


# ─────────────────────────────────────────────────────────────────────────────
# Preflight oracle gate
# ─────────────────────────────────────────────────────────────────────────────

class TestPreflight:
    @pytest.mark.asyncio
    async def test_happy_path(self, sandbox):
        oracle = await _preflight(sandbox)
        assert oracle["import_provenance"] == "ok"
        assert oracle["focus_coverage_pct"] > 0
        assert oracle["bench"]["stability"] == "stable"

    @pytest.mark.asyncio
    async def test_zero_coverage_target_refused(self, sandbox):
        with pytest.raises(PreflightError, match="never executes the focus node"):
            await _preflight(sandbox, test_target="test_nocov.py")

    @pytest.mark.asyncio
    async def test_too_slow_baseline_refused(self, sandbox):
        # Suite sleeps 2.0s; budget = 60s × 0.02 = 1.2s → killed → not passed.
        with pytest.raises(PreflightError, match="stage budget|does not pass"):
            await _preflight(sandbox, test_target="test_slow_suite.py", stage_fraction=0.02)

    @pytest.mark.asyncio
    async def test_unstable_bench_refused_by_default(self, sandbox):
        with pytest.raises(PreflightError, match="unstable"):
            await _preflight(sandbox, bench="bench_unstable.py")

    @pytest.mark.asyncio
    async def test_unstable_bench_allowed_with_flag(self, sandbox):
        # Reset the state file so the counter starts fresh.
        (sandbox.work / ".bench_state").unlink(missing_ok=True)
        oracle = await _preflight(sandbox, bench="bench_unstable.py", allow_unstable=True)
        assert oracle["bench"]["stability"] == "unstable"

    @pytest.mark.asyncio
    async def test_provenance_failure_refused(self, sandbox):
        # A module that resolves outside the work dir (import a stdlib name
        # whose file lives in the interpreter, not the sandbox).
        with pytest.raises(PreflightError, match="OUTSIDE the sandbox|cannot import"):
            await run_preflight(
                sandbox,
                target_rel="json/__init__.py",  # resolves to stdlib json, not the copy
                span_lines=(1, 5),
                test_target="test_slow.py",
                bench_argv=[sandbox.python_bin(), "bench_slow.py"],
                bench_repeats=2,
                eval_timeout=60.0,
                stage_budget_fraction=0.9,
            )


class TestNoiseFloor:
    def test_floor_is_at_least_five_percent(self):
        assert noise_floor_pct([100.0, 100.0, 100.0]) == pytest.approx(5.0)

    def test_high_variance_raises_floor(self):
        floor = noise_floor_pct([100.0, 130.0, 70.0, 120.0, 80.0])
        assert floor > 5.0

    def test_single_sample_defaults(self):
        assert noise_floor_pct([100.0]) == pytest.approx(5.0)
