# `evals/tasks/swarm_targets/slow_loop_optimize/test_loop_opt.py` refactor plan (Loop 177)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 19 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **5%** |
| Hot | `test_preserves_order` ~4 · `test_unicode_is_unchanged` ~2 · `test_single_element` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_preserves_order` | extract hot path (~4 LOC) |
| split / `test_unicode_is_unchanged` | extract hot path (~2 LOC) |
| split / `test_single_element` | extract hot path (~2 LOC) |
| `evals/tasks/swarm_targets/slow_loop_optimize/test_loop_opt.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
