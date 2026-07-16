# Superiority baseline pareto harness (prep-only)

Draft [#475](https://github.com/djtelicloud/grok-mcp-server/pull/475) — **HOLD**.
Implements Codex H1–H6 while waiting CONTINUE / #476. No production rewrite, no Swarm.

## Files

| Path | Role |
|------|------|
| `scripts/superiority_baseline_pareto.py` | Capture CLI + helpers |
| `tests/test_superiority_baseline_pareto.py` | Focused H1–H6 tests |

## Capture

```bash
uv run python scripts/superiority_baseline_pareto.py --label ORIGINAL
```

Labels: `ORIGINAL` | `TEAM_FINAL` | `SWARM_FINAL`.

## H1–H6 → G1–G10

| Prep | Behavior | Gates |
|------|----------|-------|
| H1 | Normal `import src.swarm.pareto` + exact worktree path assert | G5 / G8 |
| H2 | Full front partition + SHA-256; front0 vs brute-force `dominates` | G1 |
| H3 | ≥9 latency samples **without** tracemalloc (median + `noise_floor_pct`); peak mem separate | G2 / G3 |
| H4 | Frozen JSON receipt (`unigrok-superiority-receipt-v1`) | G5 / G7 |
| H5 | Label enum + focused tests (provenance, oracle, fixture, stats) | G6 / G1 |
| H6 | `bundle.files` + `bundle.total_loc` for one-to-many | G7 LOC |

## Out of scope (until CONTINUE)

- Rewriting `fast_non_dominated_sort`
- Starting UniGrok Swarm
- Publishing #408
- Attribution history rewrite
