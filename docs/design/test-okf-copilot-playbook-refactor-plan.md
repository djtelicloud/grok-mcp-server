# `tests/test_okf_copilot_playbook.py` refactor plan (Loop 171)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 30 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-33%** |
| Hot | `test_copilot_playbook_exists_and_covers_contract` ~17 · `test_okf_index_lists_copilot_playbook` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_copilot_playbook_exists_and_covers_contract` | extract hot path (~17 LOC) |
| split / `test_okf_index_lists_copilot_playbook` | extract hot path (~5 LOC) |
| `tests/test_okf_copilot_playbook.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
