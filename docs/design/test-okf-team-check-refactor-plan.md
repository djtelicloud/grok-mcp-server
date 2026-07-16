# `tests/test_okf_team_check.py` refactor plan (Loop 172)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 29 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-31%** |
| Hot | `test_silent_team_check_topic_covers_low_cost_review_pattern` ~16 · `test_okf_index_lists_silent_team_check` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_silent_team_check_topic_covers_low_cost_review_pattern` | extract hot path (~16 LOC) |
| split / `test_okf_index_lists_silent_team_check` | extract hot path (~5 LOC) |
| `tests/test_okf_team_check.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
