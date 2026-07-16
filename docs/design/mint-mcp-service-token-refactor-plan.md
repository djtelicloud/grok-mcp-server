# `scripts/mint_mcp_service_token.py` refactor plan (Loop 97)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 228 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `main` ~40 · `_service_access_claims` ~27 · `mint_service_access_token` ~22 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `main` |
| split module | concern from hot path `_service_access_claims` |
| split module | concern from hot path `mint_service_access_token` |
| `scripts/mint_mcp_service_token.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
