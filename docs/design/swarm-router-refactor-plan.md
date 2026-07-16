# `src/swarm/router.py` refactor plan (Loop 134)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 116 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-70%** |
| Hot | `DiscountedUCBRouter` ~77 · `reward_for` ~8 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `DiscountedUCBRouter` | extract hot path (~77 LOC) |
| split / `reward_for` | extract hot path (~8 LOC) |
| `src/swarm/router.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
