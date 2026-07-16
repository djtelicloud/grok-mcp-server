# `tests/test_codex_desktop_session_contract.py` refactor plan (Loop 158)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 51 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-31%** |
| Hot | `test_conversation_canvas_uses_host_theme_tokens_and_bounded_follow_up` ~10 · `test_codex_desktop_session_contract_is_complete` ~8 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_conversation_canvas_uses_host_theme_tokens_and_bounded_follow_up` | extract hot path (~10 LOC) |
| split / `test_codex_desktop_session_contract_is_complete` | extract hot path (~8 LOC) |
| `tests/test_codex_desktop_session_contract.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
