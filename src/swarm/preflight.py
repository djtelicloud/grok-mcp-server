"""Swarm preflight: the oracle-honesty gate that runs once per task.

Four checks, all against the sandbox copy, before any mutant is generated:

1. **Import provenance** — the focus module must resolve INSIDE the work dir
   (an editable-install `.pth` pointing at the original workspace would make
   every mutant a silently-unpatched no-op that "passes").
2. **Baseline test budget** — `test_target` must pass and fit inside
   `stage_budget_fraction × eval_timeout`, or the swarm would be a
   multi-hour zombie.
3. **Focus-span coverage** — `test_target` must actually execute the focus
   node; "verified" with 0% coverage is marketing, so it refuses. The
   percentage is recorded in oracle_json forever.
4. **Bench stability** — two full bench runs; medians differing by more than
   the noise floor mark the task `bench_unstable` (latency Pareto fronts on
   a noisy host are fiction).

Returns the oracle dict; raises PreflightError with a user-actionable
message on any refusal.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any, Dict, List, Tuple

from .sandbox import SandboxError, SwarmSandbox

_MIN_NOISE_FLOOR_PCT = 5.0


class PreflightError(RuntimeError):
    """A refusal with a user-actionable reason; partial oracle facts ride
    the .oracle attribute so status can show how far preflight got."""

    def __init__(self, message: str, oracle: Dict[str, Any]):
        super().__init__(message)
        self.oracle = dict(oracle)


def module_name_for(target_rel: str, workspace_root: Path | None = None) -> str:
    """Dotted module name for a workspace-relative path.

    A conventional ``src/`` layout puts import packages below that directory,
    while some repositories make ``src`` the import package itself.  Preserve
    the prefix when the sandbox copy proves the latter via ``src/__init__.py``.
    Non-standard layouts still fail the provenance probe loudly rather than
    guessing.
    """
    parts = list(PurePosixPath(target_rel).with_suffix("").parts)
    src_is_package = bool(
        workspace_root is not None
        and (Path(workspace_root) / "src" / "__init__.py").is_file()
    )
    if parts and parts[0] == "src" and not src_is_package:
        parts = parts[1:]
    if not parts:
        raise PreflightError(f"cannot derive a module name from {target_rel!r}", {})
    return ".".join(parts)


def noise_floor_pct(latency_samples: List[float]) -> float:
    """max(5%, 3σ relative to the median) — improvements below this are
    treated as zero everywhere (bench numbers, rewards, deltas)."""
    if len(latency_samples) < 2:
        return _MIN_NOISE_FLOOR_PCT
    median = statistics.median(latency_samples)
    if median <= 0:
        return _MIN_NOISE_FLOOR_PCT
    sigma = statistics.stdev(latency_samples)
    return max(_MIN_NOISE_FLOOR_PCT, 300.0 * sigma / median)


async def run_preflight(
    sandbox: SwarmSandbox,
    *,
    target_rel: str,
    span_lines: Tuple[int, int],
    test_target: str,
    bench_argv: List[str],
    bench_repeats: int,
    eval_timeout: float,
    stage_budget_fraction: float,
    allow_unstable_bench: bool = False,
) -> Dict[str, Any]:
    oracle: Dict[str, Any] = {}

    # 1. Import provenance — the shadowing must provably work.
    module = module_name_for(target_rel, sandbox.work)
    oracle["module"] = module
    # Compare realpaths: macOS symlinks /var -> /private/var, so a raw
    # startswith on the work-dir string would spuriously fail.
    probe = (
        f"import importlib, os, sys\n"
        f"m = importlib.import_module({module!r})\n"
        f"f = os.path.realpath(getattr(m, '__file__', '') or '')\n"
        f"root = os.path.realpath({str(sandbox.work)!r})\n"
        f"sys.exit(0 if f.startswith(root + os.sep) else 3)\n"
    )
    rc, _out, err = await sandbox.run_child(
        [sandbox.python_bin(), "-c", probe], timeout=30.0
    )
    if rc == 3:
        oracle["import_provenance"] = "shadowing_failed"
        raise PreflightError(
            f"module {module!r} imports from OUTSIDE the sandbox copy (editable "
            "install shadowing failed) — every mutant would be a no-op; check the "
            "project layout",
            oracle,
        )
    if rc != 0:
        oracle["import_provenance"] = "import_failed"
        raise PreflightError(
            f"cannot import {module!r} derived from {target_rel!r}: {err[:300]} — "
            "ensure a standard package or src/ layout",
            oracle,
        )
    oracle["import_provenance"] = "ok"

    # 2. Baseline tests inside the stage budget.
    stage_budget = max(1.0, eval_timeout * stage_budget_fraction)
    started = monotonic()
    passed, output = await sandbox.run_tests(test_target, timeout=stage_budget)
    baseline_seconds = monotonic() - started
    oracle["baseline_test_seconds"] = round(baseline_seconds, 3)
    if not passed:
        raise PreflightError(
            f"test_target {test_target!r} does not pass on the UNMODIFIED baseline "
            f"(or exceeded the {stage_budget:.0f}s stage budget): {output[-400:]}",
            oracle,
        )

    # 3. Focus-span coverage — the oracle must exercise the focus node.
    coverage_pct = await _focus_coverage_pct(
        sandbox, target_rel, span_lines, test_target, timeout=stage_budget * 2
    )
    oracle["focus_coverage_pct"] = coverage_pct
    if coverage_pct <= 0.0:
        raise PreflightError(
            f"test_target {test_target!r} never executes the focus node — "
            "feasibility would be meaningless; point test_target at tests that "
            "cover it",
            oracle,
        )

    # 4. Bench stability — two full runs, medians within the noise floor.
    run1 = await sandbox.run_bench(bench_argv, bench_repeats, eval_timeout)
    run2 = await sandbox.run_bench(bench_argv, bench_repeats, eval_timeout)
    floor = noise_floor_pct(run1["latency_samples"])
    m1, m2 = run1["latency_ms"], run2["latency_ms"]
    drift_pct = abs(m1 - m2) / m1 * 100.0 if m1 > 0 else 0.0
    stable = drift_pct <= floor
    oracle["bench"] = {
        "latency_ms": m2,
        "peak_mem_bytes": run2["peak_mem_bytes"],
        "noise_floor_pct": round(floor, 2),
        "drift_pct": round(drift_pct, 2),
        "stability": "stable" if stable else "unstable",
    }
    if not stable and not allow_unstable_bench:
        raise PreflightError(
            f"bench_command is unstable on this host (medians drifted "
            f"{drift_pct:.1f}% > noise floor {floor:.1f}%) — latency fronts would "
            "be fiction; fix the benchmark or pass allow_unstable_bench=True to "
            "optimize memory/diff only",
            oracle,
        )
    return oracle


async def _focus_coverage_pct(
    sandbox: SwarmSandbox,
    target_rel: str,
    span_lines: Tuple[int, int],
    test_target: str,
    timeout: float,
) -> float:
    python = sandbox.python_bin()
    rc, _out, err = await sandbox.run_child(
        [python, "-m", "coverage", "run", f"--include=*/{target_rel},{target_rel}",
         "-m", "pytest", "-q", "-p", "no:cacheprovider", test_target],
        timeout,
    )
    if rc != 0:
        raise SandboxError(f"coverage run failed: {err[:300]}")
    rc, out, err = await sandbox.run_child(
        [python, "-m", "coverage", "json", "-o", "coverage.json"], timeout=30.0
    )
    if rc != 0:
        # "No data to report" (coverage prints it to STDOUT) means the suite
        # never touched the target file at all — that is 0% focus-span
        # coverage, not an infrastructure error.
        if "no data to report" in (out + err).lower():
            return 0.0
        raise SandboxError(f"coverage json failed: {(err or out)[:300]}")
    try:
        report = json.loads((sandbox.work / "coverage.json").read_text())
    except (OSError, ValueError) as exc:
        raise SandboxError(f"coverage report unreadable: {exc}")
    executed: List[int] = []
    for filename, data in (report.get("files") or {}).items():
        normalized = filename.replace("\\", "/")
        if normalized.endswith(target_rel):
            executed = list(data.get("executed_lines") or [])
            break
    first, last = span_lines
    span_total = max(1, last - first + 1)
    span_hit = sum(1 for line in executed if first <= line <= last)
    return round(100.0 * span_hit / span_total, 1)
