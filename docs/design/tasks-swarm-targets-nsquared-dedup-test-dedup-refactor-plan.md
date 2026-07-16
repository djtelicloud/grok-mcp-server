# `evals/tasks/swarm_targets/nsquared_dedup/test_dedup.py` refactor plan (Loop 160)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 43 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-19%** |
| Hot | `_HashableValue` ~9 · `test_equality_crosses_hashability_categories_in_either_order` ~5 · `test_unhashable_items_preserve_first_occurrence` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `_HashableValue` | extract hot path (~9 LOC) |
| split / `test_equality_crosses_hashability_categories_in_either_order` | extract hot path (~5 LOC) |
| split / `test_unhashable_items_preserve_first_occurrence` | extract hot path (~2 LOC) |
| `evals/tasks/swarm_targets/nsquared_dedup/test_dedup.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
