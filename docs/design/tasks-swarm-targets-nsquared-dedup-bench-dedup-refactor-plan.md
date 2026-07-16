# `evals/tasks/swarm_targets/nsquared_dedup/bench_dedup.py` refactor plan (Loop 176)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 20 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **0%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `evals/tasks/swarm_targets/nsquared_dedup/bench_dedup.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
