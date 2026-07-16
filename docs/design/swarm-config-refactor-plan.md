# `src/swarm/config.py` refactor plan (Loop 118)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 156 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-78%** |
| Hot | `swarm_mode` ~15 · `validate_search_strategy` ~14 · `validate_primary_goal` ~9 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `swarm_mode` |
| split module | concern from hot path `validate_search_strategy` |
| split module | concern from hot path `validate_primary_goal` |
| `src/swarm/config.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
