#!/usr/bin/env python3
"""Opt-in helper for the swarm SWARM_BENCH contract.

Times ONE callable over a JSON args fixture under perf_counter + tracemalloc
and prints the contract line. This is the easy path, not magic: the swarm
never guesses benchmarks — you point bench_command at this script (or any
command honoring the contract).

Usage:
    python scripts/swarm_bench.py pkg.module:func --args-json '[[3,1,2]]' \
        [--kwargs-json '{}'] [--inner-loops 100]

Contract output (exactly one line on stdout):
    SWARM_BENCH {"latency_ms": <float>, "peak_mem_bytes": <int>}
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import tracemalloc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="pkg.module:function")
    parser.add_argument("--args-json", default="[]", help="JSON list of positional args")
    parser.add_argument("--kwargs-json", default="{}", help="JSON object of keyword args")
    parser.add_argument("--inner-loops", type=int, default=1,
                        help="calls per measurement (amortizes very fast functions)")
    options = parser.parse_args()

    module_name, _, func_name = options.target.partition(":")
    if not module_name or not func_name:
        print("target must be 'pkg.module:function'", file=sys.stderr)
        return 2
    func = getattr(importlib.import_module(module_name), func_name)
    args = json.loads(options.args_json)
    kwargs = json.loads(options.kwargs_json)
    loops = max(1, options.inner_loops)

    func(*args, **kwargs)  # one untimed warmup call
    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(loops):
        func(*args, **kwargs)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("SWARM_BENCH " + json.dumps({
        "latency_ms": elapsed * 1000.0 / loops,
        "peak_mem_bytes": int(peak),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
