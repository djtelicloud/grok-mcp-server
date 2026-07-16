# Superiority baseline pareto harness (prep-only)

Draft [#475](https://github.com/djtelicloud/grok-mcp-server/pull/475) — **HOLD**.
Implements Codex **H1–H10** while waiting CONTINUE / #476. No production rewrite, no Swarm.

## Files

| Path | Role |
|------|------|
| `scripts/superiority_baseline_pareto.py` | Capture CLI + helpers |
| `tests/test_superiority_baseline_pareto.py` | Focused H1–H10 tests |
| `evals/superiority/receipts/pareto_fast_non_dominated_sort_ORIGINAL.json` | **Diagnostic only** until CONTINUE |

## Capture (diagnostic smoke)

```bash
uv run python scripts/superiority_baseline_pareto.py \
  --label ORIGINAL --allow-dirty-for-smoke \
  --out evals/superiority/receipts/pareto_fast_non_dominated_sort_ORIGINAL.json
```

Receipts set `baseline_status=diagnostic_superseded` and `official_original=false`.
**Authoritative ORIGINAL** is recaptured only after Codex approves harness + posts CONTINUE,
from a clean task branch on then-current landed main.

## H1–H10 → G1–G10

| Prep | Behavior | Gates |
|------|----------|-------|
| H1 | Normal `import src.swarm.pareto` + exact worktree path assert | G5 / G8 |
| H2/H7 | Independent full front peel; every front; oracle matrix + per-case hashes | G1 |
| H3 | ≥9 latency samples without tracemalloc; traced-Python alloc separate | G2 / G3 |
| H4 | Frozen JSON receipt v2 + method identity | G5 / G7 |
| H5 | Label enum + focused regressions | G6 / G1 |
| H6/H9 | Fail-closed bundle (always include target; SHA+LOC per file) | G7 LOC |
| H8 | Refuse outer&lt;9 / inner&lt;1 / warmups&lt;1 / mem_repeats&lt;3 | G2 / G5 |
| H10 | `method_sha256`, clean-tree proof, perf matrix n=10/40/100 per-case | G5 / G7 |

## Out of scope (until CONTINUE)

- Official ORIGINAL freeze / Measured wins
- Rewriting `fast_non_dominated_sort`
- Starting UniGrok Swarm
- Publishing #408
