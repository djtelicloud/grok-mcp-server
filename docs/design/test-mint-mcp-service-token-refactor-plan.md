# `tests/test_mint_mcp_service_token.py` refactor plan (Loop 112)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 174 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-80%** |
| Hot | `test_mint_rejects_disallowed_service_and_scope` ~33 · `test_mint_service_access_token_shape_and_claims` ~25 · `test_cli_print_claims_uses_independent_non_secret_metadata` ~22 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_mint_rejects_disallowed_service_and_scope` |
| split module | concern from hot path `test_mint_service_access_token_shape_and_claims` |
| split module | concern from hot path `test_cli_print_claims_uses_independent_non_secret_metadata` |
| `tests/test_mint_mcp_service_token.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
