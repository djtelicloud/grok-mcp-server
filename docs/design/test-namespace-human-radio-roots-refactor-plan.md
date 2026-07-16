# `tests/test_namespace_human_radio_roots.py` refactor plan (Loop 166)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 35 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-43%** |
| Hot | `test_talk_to_humans_first_is_present_in_brand_roots` ~8 · `test_shared_agents_forbids_chat_pollution` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_talk_to_humans_first_is_present_in_brand_roots` | extract hot path (~8 LOC) |
| split / `test_shared_agents_forbids_chat_pollution` | extract hot path (~6 LOC) |
| `tests/test_namespace_human_radio_roots.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
