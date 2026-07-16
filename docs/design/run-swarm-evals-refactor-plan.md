# `scripts/run_swarm_evals.py` refactor plan (Loop 82)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 286 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-88%** |
| Hot | `async_main` ~47 · `discover_targets` ~37 · `payload_errors` ~30 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `async_main` |
| split module | concern from hot path `discover_targets` |
| split module | concern from hot path `payload_errors` |
| `scripts/run_swarm_evals.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
