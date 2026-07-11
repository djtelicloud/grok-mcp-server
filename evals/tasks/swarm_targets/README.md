# Swarm optimizer golden targets

Standalone mini-projects with deliberate performance anti-patterns, used to
exercise the swarm optimizer end-to-end (`UNIGROK_SWARM=dry_run`). Each folder
is a self-contained package with:

- the target module (an intentional O(N²) / over-allocating implementation),
- `test_*.py` — the correctness oracle the swarm must keep green,
- a `bench_*.py` command honoring the `SWARM_BENCH {...}` contract.

These are NOT collected by the main pytest suite (they live outside `tests/`
and the sandbox copies them into an isolated work dir). Drive one with:

```
start_code_swarm(
  target_path="evals/tasks/swarm_targets/nsquared_dedup/dedup.py",
  focus_node="function:dedup",
  test_target="evals/tasks/swarm_targets/nsquared_dedup/test_dedup.py",
  bench_command="python evals/tasks/swarm_targets/nsquared_dedup/bench_dedup.py",
)
```
