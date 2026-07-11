# Emits a wildly different latency on each invocation (state file counter) —
# used to prove the preflight bench-stability refusal.
import json
import pathlib

state = pathlib.Path(__file__).with_name(".bench_state")
count = int(state.read_text()) if state.exists() else 0
state.write_text(str(count + 1))
latency = 5.0 * (10 ** count)
print("SWARM_BENCH " + json.dumps({"latency_ms": latency, "peak_mem_bytes": 1024}))
