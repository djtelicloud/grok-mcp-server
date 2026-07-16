# `tests/campaigns/gemma_needle_2000_v1/test_stage1_artifacts.py` refactor plan (Loop 130)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 119 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-71%** |
| Hot | `test_concurrent_named_publication_has_one_immutable_value` ~18 · `test_private_content_addressed_round_trip` ~12 · `test_store_rejects_repo_and_symlink_paths` ~10 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_concurrent_named_publication_has_one_immutable_value` | extract hot path (~18 LOC) |
| split / `test_private_content_addressed_round_trip` | extract hot path (~12 LOC) |
| split / `test_store_rejects_repo_and_symlink_paths` | extract hot path (~10 LOC) |
| `tests/campaigns/gemma_needle_2000_v1/test_stage1_artifacts.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
