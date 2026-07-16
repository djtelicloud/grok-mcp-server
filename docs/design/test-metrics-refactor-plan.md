# `tests/test_metrics.py` refactor plan (Loop 67)

Status: **Ready for supervisor** — plan only. Pairs with `src/metrics.py` plan.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 388 |
| Projected primary LOC | ~50 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/metrics/test_billing_planes.py` | API vs CLI subscription separation |
| `tests/metrics/test_provider_usage.py` | SDK management alias / usage |
| `tests/metrics/test_semantic_scores.py` | semantic eval aggregate / null |
| `tests/metrics/test_telemetry_tamper.py` | attempt split round-trip / tamper |
| `tests/test_metrics.py` | shim ≤ 50 LOC |

Move-only.
