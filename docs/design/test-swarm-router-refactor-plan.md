# `tests/test_swarm_router.py` refactor plan (Loop 142)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 94 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-63%** |
| Hot | `TestRouterSelection` ~62 · `TestAlignedReward` ~12 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `TestRouterSelection` | extract hot path (~62 LOC) |
| split / `TestAlignedReward` | extract hot path (~12 LOC) |
| `tests/test_swarm_router.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
