#!/usr/bin/env python3
"""Prep-only ORIGINAL/TEAM_FINAL/SWARM_FINAL harness for pareto.fast_non_dominated_sort.

Honors Codex H1–H10 + G1–G10 provenance rules. Does **not** rewrite production
code or start Swarm. Official ORIGINAL capture waits for CONTINUE after harness
re-review (#475 HOLD).

Usage:
    uv run python scripts/superiority_baseline_pareto.py --label ORIGINAL --allow-dirty-for-smoke
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.swarm.preflight import noise_floor_pct

LABELS = ("ORIGINAL", "TEAM_FINAL", "SWARM_FINAL")
DEFAULT_OUTER_SAMPLES = 9
DEFAULT_INNER_LOOPS = 500
DEFAULT_WARMS = 1
DEFAULT_MEM_REPEATS = 3
MIN_OUTER_SAMPLES = 9
MIN_INNER_LOOPS = 1
MIN_WARMS = 1
MIN_MEM_REPEATS = 3
PERF_MATRIX_SIZES = (10, 40, 100)

# Frozen workload (deterministic). Do not edit without bumping fixture identity.
FROZEN_POINTS: List[Tuple[float, ...]] = [
    (1.0, 3.0),
    (2.0, 2.0),
    (3.0, 1.0),
    (1.5, 2.5),
    (2.5, 1.5),
    (0.5, 4.0),
    (4.0, 0.5),
    (2.0, 3.0),
    (3.0, 2.0),
    (1.0, 1.0),
]

# H7 oracle matrix — independent of production `dominates`.
ORACLE_MATRIX: Dict[str, List[Tuple[float, ...]]] = {
    "empty": [],
    "singleton": [(1.0, 1.0)],
    "duplicates_ties": [(1.0, 2.0), (1.0, 2.0), (2.0, 1.0)],
    "tradeoffs": [(1.0, 3.0), (2.0, 2.0), (3.0, 1.0)],
    "dominated_chain": [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)],
    "larger_deterministic": [
        (float(i % 5), float((i * 3) % 7), float((i * 2) % 11)) for i in range(24)
    ],
    "frozen_main": list(FROZEN_POINTS),
}


class ProvenanceError(RuntimeError):
    """Import resolved outside the expected worktree target (G5/G8)."""


class OracleError(RuntimeError):
    """Property/oracle failure (G1 / H7)."""


class CaptureParamError(ValueError):
    """Invalid capture parameters (H8)."""


class BundleError(ValueError):
    """Fail-closed bundle manifest error (H9)."""


class DirtyTreeError(RuntimeError):
    """Production/target dirty vs claimed commit (H10)."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_pareto_path() -> Path:
    return repo_root() / "src" / "swarm" / "pareto.py"


def harness_path() -> Path:
    return Path(__file__).resolve()


def focused_test_path() -> Path:
    return repo_root() / "tests" / "test_superiority_baseline_pareto.py"


def import_pareto(target_path: Optional[Path] = None):
    """H1: normal package import + exact path assert (no synthetic loaders)."""
    expected = (target_path or default_pareto_path()).resolve()
    import src.swarm.pareto as pareto_mod

    resolved = Path(getattr(pareto_mod, "__file__", "") or "").resolve()
    if not resolved.is_file() or resolved != expected:
        raise ProvenanceError(
            f"src.swarm.pareto resolved to {resolved!s}, expected exact target {expected}"
        )
    return pareto_mod


def independent_dominates(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool:
    """H7: independent minimization dominance (not production `dominates`)."""
    if len(a) != len(b):
        raise OracleError(f"objective arity mismatch: {len(a)} vs {len(b)}")
    no_worse = all(x <= y for x, y in zip(a, b))
    strictly_better = any(x < y for x, y in zip(a, b))
    return no_worse and strictly_better


def brute_force_peel(
    points: Sequence[Tuple[float, ...]],
    dominates: Callable[[Tuple[float, ...], Tuple[float, ...]], bool],
) -> List[List[int]]:
    """H7: full independent front peeling via repeated undominated extraction."""
    remaining = set(range(len(points)))
    fronts: List[List[int]] = []
    while remaining:
        front = sorted(
            i
            for i in remaining
            if not any(
                dominates(points[j], points[i]) for j in remaining if j != i
            )
        )
        if not front:
            raise OracleError("brute-force peel stuck with non-empty remaining")
        fronts.append(front)
        remaining -= set(front)
    return fronts


def normalize_fronts(fronts: Sequence[Sequence[int]]) -> List[List[int]]:
    return [sorted(list(front)) for front in fronts]


def full_front_partition(
    points: Sequence[Tuple[float, ...]],
    sort_fn,
    _production_dominates=None,
) -> Dict[str, Any]:
    """H7: compare every front vs independent peel (not front0-only)."""
    if not points:
        return {
            "fronts": [],
            "front0": [],
            "front_partition_sha256": sha256_bytes(b"[]"),
            "index_count": 0,
            "oracle": "independent_brute_force_peel",
        }
    fronts = sort_fn(list(points))
    if not isinstance(fronts, list):
        raise OracleError("fast_non_dominated_sort returned non-list fronts")
    candidate = normalize_fronts(fronts)
    expected = brute_force_peel(points, independent_dominates)
    if candidate != expected:
        raise OracleError(
            f"front partition mismatch: candidate={candidate!r} oracle={expected!r}"
        )
    flat = [i for front in candidate for i in front]
    n = len(points)
    if sorted(flat) != list(range(n)):
        raise OracleError(
            f"front partition must cover each index exactly once; got {sorted(flat)!r}"
        )
    canonical = json.dumps(candidate, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "fronts": candidate,
        "front0": list(candidate[0]) if candidate else [],
        "front_partition_sha256": digest,
        "index_count": n,
        "oracle": "independent_brute_force_peel",
    }


def oracle_matrix_hashes(
    sort_fn,
) -> Dict[str, Any]:
    """H7: freeze per-case hashes for the oracle matrix."""
    cases: Dict[str, Any] = {}
    for name, pts in ORACLE_MATRIX.items():
        result = full_front_partition(pts, sort_fn)
        cases[name] = {
            "n": len(pts),
            "front_partition_sha256": result["front_partition_sha256"],
            "front_count": len(result["fronts"]),
        }
    matrix_blob = json.dumps(cases, sort_keys=True, separators=(",", ":"))
    return {
        "cases": cases,
        "oracle_matrix_sha256": sha256_bytes(matrix_blob.encode("utf-8")),
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def fixture_sha256(points: Sequence[Tuple[float, ...]]) -> str:
    payload = json.dumps(list(points), separators=(",", ":"), ensure_ascii=True)
    return sha256_bytes(payload.encode("utf-8"))


def git_rev_parse(cwd: Path, *args: str) -> str:
    out = subprocess.check_output(
        ["git", "rev-parse", *args],
        cwd=str(cwd),
        text=True,
        stderr=subprocess.DEVNULL,
    )
    return out.strip()


def git_head(cwd: Path) -> str:
    try:
        return git_rev_parse(cwd, "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_tree_sha(cwd: Path) -> str:
    try:
        return git_rev_parse(cwd, "HEAD^{tree}")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def path_is_dirty(cwd: Path, rel: str) -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain", "--", rel],
            cwd=str(cwd),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return bool(out.strip())
    except (OSError, subprocess.CalledProcessError):
        return True


def count_loc(path: Path) -> int:
    return sum(1 for _ in path.read_text(encoding="utf-8").splitlines())


def percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def validate_capture_params(
    *,
    outer_samples: int,
    inner_loops: int,
    warmups: int,
    mem_repeats: int,
) -> None:
    """H8: refuse invalid work before any timing."""
    if outer_samples < MIN_OUTER_SAMPLES:
        raise CaptureParamError(f"outer_samples must be >= {MIN_OUTER_SAMPLES}")
    if inner_loops < MIN_INNER_LOOPS:
        raise CaptureParamError(f"inner_loops must be >= {MIN_INNER_LOOPS}")
    if warmups < MIN_WARMS:
        raise CaptureParamError(f"warmups must be >= {MIN_WARMS}")
    if mem_repeats < MIN_MEM_REPEATS:
        raise CaptureParamError(f"mem_repeats must be >= {MIN_MEM_REPEATS}")


def canonicalize_bundle(
    *,
    root: Path,
    target_path: Path,
    extra: Optional[Sequence[str]],
) -> List[Dict[str, Any]]:
    """H9: fail-closed bundle — always include target; reject abs/escape/missing."""
    root = root.resolve()
    target = target_path.resolve()
    try:
        target_rel = target.relative_to(root).as_posix()
    except ValueError as exc:
        raise BundleError(f"target escapes repo root: {target}") from exc

    requested: List[str] = list(extra or [])
    # Always include exact target; extras may omit it — we force it in.
    ordered: List[str] = []
    seen = set()

    def _add(rel: str) -> None:
        if rel in seen:
            return
        seen.add(rel)
        ordered.append(rel)

    _add(target_rel)
    for raw in requested:
        if not isinstance(raw, str) or not raw.strip():
            raise BundleError(f"empty bundle path: {raw!r}")
        if Path(raw).is_absolute() or raw.startswith("/"):
            raise BundleError(f"absolute bundle paths forbidden: {raw!r}")
        if "\\" in raw:
            raise BundleError(f"non-POSIX separators forbidden: {raw!r}")
        parts = Path(raw).parts
        if ".." in parts:
            raise BundleError(f"escaping bundle path forbidden: {raw!r}")
        rel = Path(*parts).as_posix() if parts else ""
        if not rel:
            raise BundleError(f"empty canonical path from {raw!r}")
        abs_path = (root / rel).resolve()
        try:
            abs_path.relative_to(root)
        except ValueError as exc:
            raise BundleError(f"path escapes repo: {raw!r}") from exc
        if not abs_path.is_file():
            raise BundleError(f"bundle path missing or not a file: {rel}")
        _add(rel)

    entries: List[Dict[str, Any]] = []
    for rel in ordered:
        path = root / rel
        entries.append(
            {
                "path": rel,
                "loc": count_loc(path),
                "sha256": sha256_file(path),
            }
        )
    return entries


def measure_latency_ms(
    fn,
    points: Sequence[Tuple[float, ...]],
    *,
    outer_samples: int,
    inner_loops: int,
    warmups: int,
) -> Dict[str, Any]:
    """H3: latency-only runs (no tracemalloc)."""
    args = (list(points),)
    for _ in range(warmups):
        fn(*args)
    samples: List[float] = []
    for _ in range(outer_samples):
        started = time.perf_counter()
        for _ in range(inner_loops):
            fn(*args)
        elapsed = time.perf_counter() - started
        samples.append((elapsed * 1000.0) / inner_loops)
    ordered = sorted(samples)
    median = float(statistics.median(samples))
    return {
        "samples_ms": samples,
        "median_ms": median,
        "p50_ms": float(percentile(ordered, 50)),
        "p95_ms": float(percentile(ordered, 95)),
        "noise_floor_pct": float(noise_floor_pct(samples)),
        "outer_samples": outer_samples,
        "inner_loops": inner_loops,
        "warmups": warmups,
        "tracemalloc": False,
    }


def measure_traced_python_alloc_bytes(
    fn,
    points: Sequence[Tuple[float, ...]],
    *,
    repeats: int,
    inner_loops: int,
    warmups: int,
) -> Dict[str, Any]:
    """H3/H10: traced Python allocation (not RSS) in separate runs."""
    args = (list(points),)
    for _ in range(warmups):
        fn(*args)
    peaks: List[int] = []
    for _ in range(repeats):
        tracemalloc.start()
        for _ in range(inner_loops):
            fn(*args)
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(int(peak))
    return {
        "metric": "traced_python_allocation_bytes",
        "samples_traced_python_alloc_bytes": peaks,
        "peak_traced_python_alloc_bytes": int(max(peaks) if peaks else 0),
        "median_traced_python_alloc_bytes": int(statistics.median(peaks)) if peaks else 0,
        "repeats": repeats,
        "inner_loops": inner_loops,
        "warmups": warmups,
        "method": "tracemalloc_separate_from_latency",
        # Legacy aliases for earlier receipt readers
        "samples_peak_mem_bytes": peaks,
        "peak_mem_bytes": int(max(peaks) if peaks else 0),
        "median_peak_mem_bytes": int(statistics.median(peaks)) if peaks else 0,
    }


def synth_points(n: int) -> List[Tuple[float, ...]]:
    """Deterministic larger workloads for the performance matrix."""
    return [
        (float(i % 7), float((i * 3) % 11), float((i * 5) % 13)) for i in range(n)
    ]


def run_performance_matrix(
    sort_fn,
    *,
    outer_samples: int,
    inner_loops: int,
    warmups: int,
    mem_repeats: int,
) -> Dict[str, Any]:
    """H10: per-case latency + traced-Python memory; no aggregate hide."""
    cases: Dict[str, Any] = {}
    for n in PERF_MATRIX_SIZES:
        pts = synth_points(n)
        full_front_partition(pts, sort_fn)  # parity gate per case
        latency = measure_latency_ms(
            sort_fn,
            pts,
            outer_samples=outer_samples,
            inner_loops=max(inner_loops, 1) if n <= 40 else max(20, inner_loops // 10),
            warmups=warmups,
        )
        memory = measure_traced_python_alloc_bytes(
            sort_fn,
            pts,
            repeats=mem_repeats,
            inner_loops=max(5, min(inner_loops, 50)),
            warmups=warmups,
        )
        cases[f"n={n}"] = {
            "n": n,
            "fixture_sha256": fixture_sha256(pts),
            "latency": latency,
            "memory": memory,
        }
    return {
        "sizes": list(PERF_MATRIX_SIZES),
        "cases": cases,
        "note": "per-case only; do not score wins from aggregates",
    }


def compute_method_identity(
    *,
    outer_samples: int,
    inner_loops: int,
    warmups: int,
    mem_repeats: int,
    oracle_matrix: Dict[str, Any],
) -> Dict[str, Any]:
    """H10: method hash independent of candidate source."""
    harness = harness_path()
    tests = focused_test_path()
    params = {
        "outer_samples": outer_samples,
        "inner_loops": inner_loops,
        "warmups": warmups,
        "mem_repeats": mem_repeats,
        "perf_matrix_sizes": list(PERF_MATRIX_SIZES),
        "frozen_fixture_sha256": fixture_sha256(FROZEN_POINTS),
        "oracle_matrix_sha256": oracle_matrix["oracle_matrix_sha256"],
    }
    parts = {
        "harness_sha256": sha256_file(harness),
        "focused_test_sha256": sha256_file(tests) if tests.is_file() else "",
        "fixture_sha256": fixture_sha256(FROZEN_POINTS),
        "oracle_matrix_sha256": oracle_matrix["oracle_matrix_sha256"],
        "parameters": params,
    }
    method_blob = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    parts["method_sha256"] = sha256_bytes(method_blob.encode("utf-8"))
    return parts


def assert_clean_paths(
    root: Path,
    bundle_entries: Sequence[Dict[str, Any]],
    *,
    allow_dirty: bool,
) -> None:
    """H10: refuse dirty target/bundle claiming a clean commit."""
    if allow_dirty:
        return
    dirty = [e["path"] for e in bundle_entries if path_is_dirty(root, e["path"])]
    if dirty:
        raise DirtyTreeError(
            f"dirty target/bundle paths vs git index: {dirty}; "
            "refuse capture (or pass allow_dirty for smoke only)"
        )


def build_receipt(
    *,
    label: str,
    pareto_mod,
    target_path: Path,
    points: Sequence[Tuple[float, ...]],
    latency: Dict[str, Any],
    memory: Dict[str, Any],
    oracle: Dict[str, Any],
    oracle_matrix: Dict[str, Any],
    performance_matrix: Dict[str, Any],
    method: Dict[str, Any],
    bundle_entries: Sequence[Dict[str, Any]],
    baseline_status: str,
) -> Dict[str, Any]:
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}, got {label!r}")
    root = repo_root()
    return {
        "schema": "unigrok-superiority-receipt-v2",
        "baseline_status": baseline_status,
        "official_original": False,
        "label": label,
        "gates": [
            "G1",
            "G2",
            "G3",
            "G5",
            "G7",
            "H1",
            "H2",
            "H3",
            "H4",
            "H5",
            "H6",
            "H7",
            "H8",
            "H9",
            "H10",
        ],
        "entry_point": "src.swarm.pareto:fast_non_dominated_sort",
        "source_path": str(target_path.resolve()),
        "source_sha256": sha256_file(target_path),
        "fixture_sha256": fixture_sha256(points),
        "frozen_inputs": {"points": [list(p) for p in points]},
        "git_commit": git_head(root),
        "git_tree_sha": git_tree_sha(root),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
        },
        "module_file": str(Path(pareto_mod.__file__).resolve()),
        "oracle": oracle,
        "oracle_matrix": oracle_matrix,
        "method": method,
        "latency": latency,
        "memory": memory,
        "performance_matrix": performance_matrix,
        "bundle": {
            "files": [e["path"] for e in bundle_entries],
            "entries": list(bundle_entries),
            "loc_by_file": {e["path"]: e["loc"] for e in bundle_entries},
            "sha256_by_file": {e["path"]: e["sha256"] for e in bundle_entries},
            "total_loc": int(sum(e["loc"] for e in bundle_entries)),
        },
        "parity": {
            "property_pass": True,
            "note": "independent full peel vs candidate fronts (H7/G1)",
        },
        "capture_note": (
            "H7–H10 harness smoke/diagnostic until Codex re-reviews and posts "
            "CONTINUE; authoritative ORIGINAL is then recaptured from clean "
            "branch on landed main."
        ),
    }


def run_capture(
    *,
    label: str,
    target_path: Optional[Path] = None,
    outer_samples: int = DEFAULT_OUTER_SAMPLES,
    inner_loops: int = DEFAULT_INNER_LOOPS,
    warmups: int = DEFAULT_WARMS,
    mem_repeats: int = DEFAULT_MEM_REPEATS,
    bundle_files: Optional[Sequence[str]] = None,
    allow_dirty: bool = False,
    include_perf_matrix: bool = True,
    baseline_status: str = "diagnostic_superseded",
) -> Dict[str, Any]:
    validate_capture_params(
        outer_samples=outer_samples,
        inner_loops=inner_loops,
        warmups=warmups,
        mem_repeats=mem_repeats,
    )
    root = repo_root()
    target = (target_path or default_pareto_path()).resolve()
    bundle_entries = canonicalize_bundle(
        root=root, target_path=target, extra=bundle_files
    )
    assert_clean_paths(root, bundle_entries, allow_dirty=allow_dirty)

    pareto_mod = import_pareto(target)
    sort_fn = pareto_mod.fast_non_dominated_sort
    points = list(FROZEN_POINTS)
    oracle = full_front_partition(points, sort_fn)
    oracle_matrix = oracle_matrix_hashes(sort_fn)
    method = compute_method_identity(
        outer_samples=outer_samples,
        inner_loops=inner_loops,
        warmups=warmups,
        mem_repeats=mem_repeats,
        oracle_matrix=oracle_matrix,
    )
    latency = measure_latency_ms(
        sort_fn,
        points,
        outer_samples=outer_samples,
        inner_loops=inner_loops,
        warmups=warmups,
    )
    memory = measure_traced_python_alloc_bytes(
        sort_fn,
        points,
        repeats=mem_repeats,
        inner_loops=inner_loops,
        warmups=warmups,
    )
    perf = (
        run_performance_matrix(
            sort_fn,
            outer_samples=outer_samples,
            inner_loops=inner_loops,
            warmups=warmups,
            mem_repeats=mem_repeats,
        )
        if include_perf_matrix
        else {"sizes": [], "cases": {}, "note": "skipped"}
    )
    return build_receipt(
        label=label,
        pareto_mod=pareto_mod,
        target_path=target,
        points=points,
        latency=latency,
        memory=memory,
        oracle=oracle,
        oracle_matrix=oracle_matrix,
        performance_matrix=perf,
        method=method,
        bundle_entries=bundle_entries,
        baseline_status=baseline_status,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        required=True,
        choices=LABELS,
        help="Stage label (ORIGINAL | TEAM_FINAL | SWARM_FINAL)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Exact pareto.py path (defaults to worktree src/swarm/pareto.py)",
    )
    parser.add_argument("--outer-samples", type=int, default=DEFAULT_OUTER_SAMPLES)
    parser.add_argument("--inner-loops", type=int, default=DEFAULT_INNER_LOOPS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMS)
    parser.add_argument("--mem-repeats", type=int, default=DEFAULT_MEM_REPEATS)
    parser.add_argument(
        "--bundle-file",
        action="append",
        default=None,
        help="Extra relative paths for one-to-many bundle (repeatable)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON receipt to this path (also prints to stdout)",
    )
    parser.add_argument(
        "--allow-dirty-for-smoke",
        action="store_true",
        help="Allow dirty harness files for diagnostic smoke only (not official ORIGINAL)",
    )
    parser.add_argument(
        "--skip-perf-matrix",
        action="store_true",
        help="Skip n=10/40/100 matrix (tests only; not for official capture)",
    )
    options = parser.parse_args(list(argv) if argv is not None else None)

    try:
        validate_capture_params(
            outer_samples=options.outer_samples,
            inner_loops=options.inner_loops,
            warmups=options.warmups,
            mem_repeats=options.mem_repeats,
        )
    except CaptureParamError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        receipt = run_capture(
            label=options.label,
            target_path=options.target,
            outer_samples=options.outer_samples,
            inner_loops=options.inner_loops,
            warmups=options.warmups,
            mem_repeats=options.mem_repeats,
            bundle_files=options.bundle_file,
            allow_dirty=options.allow_dirty_for_smoke,
            include_perf_matrix=not options.skip_perf_matrix,
            baseline_status="diagnostic_superseded",
        )
    except (CaptureParamError, BundleError, DirtyTreeError, ProvenanceError, OracleError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(text)
    if options.out is not None:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
