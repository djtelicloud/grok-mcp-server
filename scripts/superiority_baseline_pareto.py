#!/usr/bin/env python3
"""Prep-only ORIGINAL/TEAM_FINAL/SWARM_FINAL harness for pareto.fast_non_dominated_sort.

Honors Codex H1–H6 + G1–G10 provenance rules. Does **not** rewrite production
code or start Swarm — capture frozen receipts while HOLD.

Usage:
    uv run python scripts/superiority_baseline_pareto.py --label ORIGINAL
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.swarm.preflight import noise_floor_pct

LABELS = ("ORIGINAL", "TEAM_FINAL", "SWARM_FINAL")
DEFAULT_OUTER_SAMPLES = 9
DEFAULT_INNER_LOOPS = 500
DEFAULT_WARMS = 1
DEFAULT_MEM_REPEATS = 5

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


class ProvenanceError(RuntimeError):
    """Import resolved outside the expected worktree target (G5/G8)."""


class OracleError(RuntimeError):
    """Property/oracle failure (G1)."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_pareto_path() -> Path:
    return repo_root() / "src" / "swarm" / "pareto.py"


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


def brute_force_front0(points: Sequence[Tuple[float, ...]], dominates) -> List[int]:
    """Independent front-0 oracle (minimization)."""
    front: List[int] = []
    for i, p in enumerate(points):
        if not any(dominates(points[j], p) for j in range(len(points)) if j != i):
            front.append(i)
    return sorted(front)


def full_front_partition(
    points: Sequence[Tuple[float, ...]], sort_fn, dominates
) -> Dict[str, Any]:
    """H2: complete fronts + SHA + front0 vs brute-force."""
    fronts = sort_fn(list(points))
    if not isinstance(fronts, list) or not fronts:
        raise OracleError("fast_non_dominated_sort returned empty/non-list fronts")
    flat: List[int] = []
    for front in fronts:
        if not isinstance(front, list):
            raise OracleError("front partition must be list[list[int]]")
        flat.extend(front)
    n = len(points)
    if sorted(flat) != list(range(n)):
        raise OracleError(
            f"front partition must cover each index exactly once; got {sorted(flat)!r}"
        )
    brute0 = brute_force_front0(points, dominates)
    if sorted(fronts[0]) != brute0:
        raise OracleError(
            f"front0 {sorted(fronts[0])!r} != brute-force oracle {brute0!r}"
        )
    canonical = json.dumps(fronts, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "fronts": fronts,
        "front0": list(fronts[0]),
        "front_partition_sha256": digest,
        "index_count": n,
        "oracle": "brute_force_dominates+partition_sha256",
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def fixture_sha256(points: Sequence[Tuple[float, ...]]) -> str:
    payload = json.dumps(list(points), separators=(",", ":"), ensure_ascii=True)
    return sha256_bytes(payload.encode("utf-8"))


def git_head(cwd: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


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
    for _ in range(max(0, warmups)):
        fn(*args)
    samples: List[float] = []
    for _ in range(outer_samples):
        started = time.perf_counter()
        for _ in range(inner_loops):
            fn(*args)
        elapsed = time.perf_counter() - started
        samples.append((elapsed * 1000.0) / max(1, inner_loops))
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


def measure_peak_mem_bytes(
    fn,
    points: Sequence[Tuple[float, ...]],
    *,
    repeats: int,
    inner_loops: int,
    warmups: int,
) -> Dict[str, Any]:
    """H3: peak memory in separate repeated runs (tracemalloc)."""
    args = (list(points),)
    for _ in range(max(0, warmups)):
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
        "samples_peak_mem_bytes": peaks,
        "peak_mem_bytes": int(max(peaks) if peaks else 0),
        "median_peak_mem_bytes": int(statistics.median(peaks)) if peaks else 0,
        "repeats": repeats,
        "inner_loops": inner_loops,
        "warmups": warmups,
        "method": "tracemalloc_separate_from_latency",
    }


def build_receipt(
    *,
    label: str,
    pareto_mod,
    target_path: Path,
    points: Sequence[Tuple[float, ...]],
    latency: Dict[str, Any],
    memory: Dict[str, Any],
    oracle: Dict[str, Any],
    bundle_files: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """H4 + H6: frozen JSON receipt (bundle-ready)."""
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}, got {label!r}")
    root = repo_root()
    files = list(bundle_files) if bundle_files else [str(target_path.relative_to(root))]
    loc_by_file = {
        rel: count_loc(root / rel) for rel in files if (root / rel).is_file()
    }
    return {
        "schema": "unigrok-superiority-receipt-v1",
        "label": label,
        "gates": ["G1", "G2", "G3", "G5", "G7", "H1", "H2", "H3", "H4", "H5", "H6"],
        "entry_point": "src.swarm.pareto:fast_non_dominated_sort",
        "source_path": str(target_path.resolve()),
        "source_sha256": sha256_file(target_path),
        "fixture_sha256": fixture_sha256(points),
        "frozen_inputs": {"points": [list(p) for p in points]},
        "git_commit": git_head(root),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
        },
        "module_file": str(Path(pareto_mod.__file__).resolve()),
        "oracle": oracle,
        "latency": latency,
        "memory": memory,
        "bundle": {
            "files": files,
            "loc_by_file": loc_by_file,
            "total_loc": int(sum(loc_by_file.values())),
        },
        "parity": {
            "property_pass": True,
            "note": "full-front partition + brute-force front0 (G1)",
        },
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
) -> Dict[str, Any]:
    target = (target_path or default_pareto_path()).resolve()
    pareto_mod = import_pareto(target)
    sort_fn = pareto_mod.fast_non_dominated_sort
    dominates = pareto_mod.dominates
    points = list(FROZEN_POINTS)
    oracle = full_front_partition(points, sort_fn, dominates)
    latency = measure_latency_ms(
        sort_fn,
        points,
        outer_samples=outer_samples,
        inner_loops=inner_loops,
        warmups=warmups,
    )
    memory = measure_peak_mem_bytes(
        sort_fn,
        points,
        repeats=mem_repeats,
        inner_loops=inner_loops,
        warmups=warmups,
    )
    return build_receipt(
        label=label,
        pareto_mod=pareto_mod,
        target_path=target,
        points=points,
        latency=latency,
        memory=memory,
        oracle=oracle,
        bundle_files=bundle_files,
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
        help="Extra relative paths for one-to-many bundle LOC (repeatable)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON receipt to this path (also prints to stdout)",
    )
    options = parser.parse_args(list(argv) if argv is not None else None)

    if options.outer_samples < 9:
        print("--outer-samples must be >= 9 (H3)", file=sys.stderr)
        return 2

    receipt = run_capture(
        label=options.label,
        target_path=options.target,
        outer_samples=options.outer_samples,
        inner_loops=options.inner_loops,
        warmups=options.warmups,
        mem_repeats=options.mem_repeats,
        bundle_files=options.bundle_file,
    )
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(text)
    if options.out is not None:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
