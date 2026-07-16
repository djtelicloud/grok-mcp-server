# `evals/tasks/swarm_targets/slow_loop_optimize/loop_opt.py` refactor plan (Loop 180)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 15 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **33%** |
| Hot | `slow_accumulate` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `slow_accumulate` | extract hot path (~6 LOC) |
| `evals/tasks/swarm_targets/slow_loop_optimize/loop_opt.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
