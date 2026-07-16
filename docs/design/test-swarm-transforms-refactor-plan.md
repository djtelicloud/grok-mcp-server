# `tests/test_swarm_transforms.py` refactor plan (Loop 156)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 51 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-31%** |
| Hot | `test_append_loop_and_reverse_transform_are_parseable` ~16 · `test_append_loop_rejects_self_referential_ordered_dedup` ~10 · `test_method_indentation_is_preserved` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_append_loop_and_reverse_transform_are_parseable` | extract hot path (~16 LOC) |
| split / `test_append_loop_rejects_self_referential_ordered_dedup` | extract hot path (~10 LOC) |
| split / `test_method_indentation_is_preserved` | extract hot path (~5 LOC) |
| `tests/test_swarm_transforms.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
