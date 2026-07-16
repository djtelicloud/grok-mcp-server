# `src/metrics.py` refactor plan (Loop 63)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 422 |
| Projected primary LOC | ~55 facade |
| % LOC change (primary file) | **−87%** |
| Hot | `fetch_provider_api_usage` ~90 · `_aggregate` ~86 · `build_metrics_snapshot` ~68 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/metrics_aggregate.py` | `_aggregate` / aggregate_telemetry_callers |
| `src/metrics_provider_usage.py` | fetch_provider_api_usage |
| `src/metrics_snapshot.py` | build_metrics_snapshot |
| `src/metrics.py` | facade ≤ 55 LOC |

Move-only.
