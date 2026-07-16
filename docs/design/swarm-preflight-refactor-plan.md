# `src/swarm/preflight.py` refactor plan (Loop 104)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 203 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-83%** |
| Hot | `run_preflight` ~95 · `_focus_coverage_pct` ~39 · `noise_floor_pct` ~10 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `run_preflight` |
| split module | concern from hot path `_focus_coverage_pct` |
| split module | concern from hot path `noise_floor_pct` |
| `src/swarm/preflight.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
