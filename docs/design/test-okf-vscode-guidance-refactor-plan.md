# `tests/test_okf_vscode_guidance.py` refactor plan (Loop 170)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 32 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-38%** |
| Hot | `test_agent_tool_documents_vscode_copilot_patterns` ~9 · `test_metrics_tool_documents_ide_session_checks` ~7 · `test_chat_modes_points_http_clients_to_agent_entrypoint` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_agent_tool_documents_vscode_copilot_patterns` | extract hot path (~9 LOC) |
| split / `test_metrics_tool_documents_ide_session_checks` | extract hot path (~7 LOC) |
| split / `test_chat_modes_points_http_clients_to_agent_entrypoint` | extract hot path (~6 LOC) |
| `tests/test_okf_vscode_guidance.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
