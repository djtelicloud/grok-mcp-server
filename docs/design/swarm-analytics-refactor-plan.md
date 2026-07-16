# `src/swarm/analytics.py` refactor plan (Loop 78)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 299 |
| Projected primary LOC | ~40 facade |
| % LOC change (primary file) | **−87%** |
| Hot | `analyze_python_source` ~52 · `_function_inventory` ~36 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/swarm/analytics_complexity.py` | `_complexity` |
| `src/swarm/analytics_inventory.py` | `_function_inventory` |
| `src/swarm/analytics_source.py` | analyze_python_source |
| `src/swarm/analytics_ruff.py` | add_ruff_summary |
| `src/swarm/analytics.py` | facade ≤ 40 LOC |

Move-only.
