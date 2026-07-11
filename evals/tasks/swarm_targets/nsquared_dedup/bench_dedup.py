"""SWARM_BENCH command for the dedup target: times dedup over a large
list with many duplicates (where O(N^2) hurts)."""
import json
import time
import tracemalloc

from dedup import dedup

workload = list(range(400)) * 3

dedup(workload[:50])  # warmup
tracemalloc.start()
started = time.perf_counter()
for _ in range(20):
    dedup(workload)
elapsed_ms = (time.perf_counter() - started) * 1000.0 / 20
_current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

print("SWARM_BENCH " + json.dumps({"latency_ms": elapsed_ms, "peak_mem_bytes": int(peak)}))
