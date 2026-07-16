# `scripts/swarm_bench.py` refactor plan (Loop 154)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 62 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-44%** |
| Hot | `main` ~32 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `main` | extract hot path (~32 LOC) |
| `scripts/swarm_bench.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
