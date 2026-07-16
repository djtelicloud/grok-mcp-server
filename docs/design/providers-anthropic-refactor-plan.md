# `src/providers/anthropic.py` refactor plan (Loop 121)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 151 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-77%** |
| Hot | `AnthropicAdapter` ~121 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `AnthropicAdapter` |
| `src/providers/anthropic.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
