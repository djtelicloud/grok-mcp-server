# `tests/test_observability.py` refactor plan (Loop 36)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 712 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−87%** |
| Classes / tests | 13 / ~50 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/observability/test_prometheus.py` | TestPrometheusRendering / TaskRagPrometheus |
| `tests/observability/test_request_ids.py` | Gateway + Agent entrypoint request ids |
| `tests/observability/` remaining class files | other suites |
| `tests/test_observability.py` | shim ≤ 90 LOC |

Move-only.
