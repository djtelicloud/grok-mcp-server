# `tests/test_harness.py` refactor plan (Loop 98)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 231 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `test_cloudrun_orchestrate_does_not_cli_fallback` ~32 · `test_run_agent_turn_preserves_openai_message_context` ~28 · `test_run_agent_turn_persists_session_turn` ~27 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_cloudrun_orchestrate_does_not_cli_fallback` |
| split module | concern from hot path `test_run_agent_turn_preserves_openai_message_context` |
| split module | concern from hot path `test_run_agent_turn_persists_session_turn` |
| `tests/test_harness.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
