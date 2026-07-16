# `tests/test_claude_md_runtime_contract.py` refactor plan (Loop 179)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 15 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **33%** |
| Hot | `test_claude_md_documents_cli_readiness_and_session_continuity` ~9 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_claude_md_documents_cli_readiness_and_session_continuity` | extract hot path (~9 LOC) |
| `tests/test_claude_md_runtime_contract.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
