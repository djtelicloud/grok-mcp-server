# `evals/tasks/swarm_targets/nsquared_dedup/dedup.py` refactor plan (Loop 181)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 11 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **82%** |
| Hot | `dedup` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `dedup` | extract hot path (~6 LOC) |
| `evals/tasks/swarm_targets/nsquared_dedup/dedup.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
