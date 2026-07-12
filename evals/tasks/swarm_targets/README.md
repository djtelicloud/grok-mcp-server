# Swarm optimizer golden targets

Standalone mini-projects with deliberate performance anti-patterns, used to
exercise the swarm optimizer end-to-end (`UNIGROK_SWARM=dry_run`). Each folder
is a self-contained package with:

- the target module (an intentional O(N²) / over-allocating implementation),
- `test_*.py` — the correctness oracle the swarm must keep green,
- a `bench_*.py` command honoring the `SWARM_BENCH {...}` contract.
- `target.json` — a versioned manifest naming the target, AST focus node,
  correctness oracle, and benchmark script explicitly.

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

Registered targets:

- `nsquared_dedup` — order-preserving list de-duplication with an O(N²)
  membership scan.
- `slow_loop_optimize` — repeated full-string copying and intermediate-list
  allocation in a hot loop.

The opt-in live sweep discovers these manifests and runs targets sequentially:

```bash
docker compose -f docker-compose.dev.yml exec \
  -e UNIGROK_SWARM_EVALS_LIVE=1 \
  -e UNIGROK_SWARM=dry_run \
  grok-mcp /app/.venv/bin/python scripts/run_swarm_evals.py
```

This consumes real CLI subscription quota and wall-clock time even though the
UniGrok receipt cost must remain exactly `$0`. It is manual/nightly evidence,
never a CI merge gate.
