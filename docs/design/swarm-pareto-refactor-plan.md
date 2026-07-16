# `src/swarm/pareto.py` refactor plan (Loop 115)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 169 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-79%** |
| Hot | `select_champion` ~43 · `fast_non_dominated_sort` ~30 · `crowding_distance` ~24 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `select_champion` |
| split module | concern from hot path `fast_non_dominated_sort` |
| split module | concern from hot path `crowding_distance` |
| `src/swarm/pareto.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
