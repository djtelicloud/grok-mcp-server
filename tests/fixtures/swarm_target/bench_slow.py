# SWARM_BENCH contract fixture: deterministic output so sandbox tests are
# stable (real measurement is scripts/swarm_bench.py's job — the sandbox
# only enforces the contract).
import json

from slow_mod import slow_sort

slow_sort(list(range(50, 0, -1)))
print("setup log line (ignored by the parser)")
print("SWARM_BENCH " + json.dumps({"latency_ms": 5.0, "peak_mem_bytes": 2048}))
