#!/usr/bin/env python3
"""ORIGINAL / TEAM_FINAL / SWARM_FINAL metric capture for pareto.fast_non_dominated_sort.

Prep stub for the proper team→swarm rerun. Does not rewrite code.
Prefer running from the python-superiority-loop worktree after #476 so
``from src.swarm.pareto import ...`` resolves (workspace root on sys.path).

Usage:
  python scripts/superiority_baseline_pareto.py
  python scripts/superiority_baseline_pareto.py --label ORIGINAL
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARETO_PATH = ROOT / "src" / "swarm" / "pareto.py"


def _parse_ms(source: str) -> float:
    t0 = time.perf_counter()
    ast.parse(source)
    return (time.perf_counter() - t0) * 1000.0


def _import_pareto():
    """Load pareto without importing src/__init__.py (avoids FastMCP side effects).

    Forge/swarm after #476 resolves ``src.swarm.pareto`` with workspace root on
    PYTHONPATH. Local smoke uses importlib under that module name for honesty.
    """
    import importlib.util

    name = "src.swarm.pareto"
    # Ensure parent packages exist as namespaces without executing src/__init__.py
    for pkg, path in (
        ("src", ROOT / "src"),
        ("src.swarm", ROOT / "src" / "swarm"),
    ):
        if pkg not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                pkg,
                path / "__init__.py",
                submodule_search_locations=[str(path)],
            )
            if spec is None or spec.loader is None:
                # namespace package fallback
                mod = type(sys)("module")
                mod.__path__ = [str(path)]  # type: ignore[attr-defined]
                sys.modules[pkg] = mod
            else:
                # Prefer namespace-only for src to skip heavy __init__
                if pkg == "src":
                    mod = type(sys)("module")
                    mod.__path__ = [str(path)]  # type: ignore[attr-defined]
                    mod.__package__ = pkg
                    sys.modules[pkg] = mod
                else:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[pkg] = mod
                    # swarm/__init__ is light; safe to exec if present
                    try:
                        spec.loader.exec_module(mod)
                    except Exception:
                        mod.__path__ = [str(path)]  # type: ignore[attr-defined]

    spec = importlib.util.spec_from_file_location(name, PARETO_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PARETO_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod.fast_non_dominated_sort, name


def measure(label: str, repeats: int = 200, n_points: int = 40) -> dict:
    source = PARETO_PATH.read_text(encoding="utf-8")
    loc = len(source.splitlines())
    parse_ms = _parse_ms(source)

    t0 = time.perf_counter()
    fn, import_path = _import_pareto()
    import_ms = (time.perf_counter() - t0) * 1000.0

    rng = random.Random(0)
    pts = [
        (rng.random() * 100.0, rng.random() * 1e6, rng.random() * 5000.0)
        for _ in range(n_points)
    ]

    # warmup
    fn(pts)

    tracemalloc.start()
    t0 = time.perf_counter()
    fronts = None
    for _ in range(repeats):
        fronts = fn(pts)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "label": label,
        "path": "src/swarm/pareto.py",
        "symbol": "fast_non_dominated_sort",
        "loc": loc,
        "parse_ms": round(parse_ms, 4),
        "import_ms": round(import_ms, 4),
        "import_path": import_path,
        "microbench_ms_per_call": round(elapsed_ms / repeats, 6),
        "microbench_repeats": repeats,
        "n_points": n_points,
        "peak_tracemalloc_bytes": int(peak),
        "front0_size": len(fronts[0]) if fronts else None,
        "note": "Not a Measured win until ORIGINAL→TEAM→SWARM deltas are recorded after CONTINUE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="ORIGINAL", help="ORIGINAL | TEAM_FINAL | SWARM_FINAL")
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--points", type=int, default=40)
    args = parser.parse_args()
    result = measure(args.label, repeats=args.repeats, n_points=args.points)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["import_path"].startswith("swarm.pareto"):
        print(
            "\nWARN: imported via bare swarm.pareto — re-run after #476 Live "
            "so src.swarm.pareto is the Forge path.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
