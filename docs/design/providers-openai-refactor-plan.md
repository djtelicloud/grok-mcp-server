# `src/providers/openai.py` refactor plan (Loop 119)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 154 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-77%** |
| Hot | `OpenAIAdapter` ~126 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `OpenAIAdapter` |
| `src/providers/openai.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
