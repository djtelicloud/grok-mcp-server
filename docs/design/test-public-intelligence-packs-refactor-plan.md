# `tests/test_public_intelligence_packs.py` refactor plan (Loop 153)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 64 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-45%** |
| Hot | `test_manifest_and_bodies_exist_and_match_schema_shape` ~34 · `test_using_unigrok_skills_are_identical` ~6 · `test_readme_states_promote_not_auto_sync` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_manifest_and_bodies_exist_and_match_schema_shape` | extract hot path (~34 LOC) |
| split / `test_using_unigrok_skills_are_identical` | extract hot path (~6 LOC) |
| split / `test_readme_states_promote_not_auto_sync` | extract hot path (~5 LOC) |
| `tests/test_public_intelligence_packs.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
