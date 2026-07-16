# `evals/campaigns/gemma_needle_2000_v1/provider_transports.py` refactor plan (Loop 84)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 273 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-87%** |
| Hot | `UniGrokMCPTransport` ~117 · `VertexADCTransport` ~89 · `_integer_setting` ~14 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `UniGrokMCPTransport` |
| split module | concern from hot path `VertexADCTransport` |
| split module | concern from hot path `_integer_setting` |
| `evals/campaigns/gemma_needle_2000_v1/provider_transports.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
