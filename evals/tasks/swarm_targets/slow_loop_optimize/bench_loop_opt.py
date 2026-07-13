"""SWARM_BENCH command for the repeated-allocation loop target."""

import json
import time
import tracemalloc

from loop_opt import slow_accumulate


workload = [f"record-{index:05d}" for index in range(8_000)]

slow_accumulate(workload[:100])  # warmup
tracemalloc.start()
started = time.perf_counter()
for _ in range(20):
    slow_accumulate(workload)
elapsed_ms = (time.perf_counter() - started) * 1000.0 / 20
_current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

print(
    "SWARM_BENCH "
    + json.dumps({"latency_ms": elapsed_ms, "peak_mem_bytes": int(peak)})
)
