# `evals/tasks/swarm_targets/slow_loop_optimize/bench_loop_opt.py` refactor plan (Loop 174)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 24 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-17%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `evals/tasks/swarm_targets/slow_loop_optimize/bench_loop_opt.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
