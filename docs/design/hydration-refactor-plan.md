# `src/hydration.py` refactor plan (Loop 107)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 194 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-82%** |
| Hot | `HydrationService` ~115 · `get_hydration_service` ~16 · `HydrationHook` ~7 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `HydrationService` |
| split module | concern from hot path `get_hydration_service` |
| split module | concern from hot path `HydrationHook` |
| `src/hydration.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
