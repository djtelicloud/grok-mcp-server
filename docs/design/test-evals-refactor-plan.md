# `tests/test_evals.py` refactor plan (Loop 26)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~926 LOC**, **9** classes, **~46** tests. Split offline replay, routing advisor, calibration.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 926 |
| Projected primary LOC | ~100 shim |
| % LOC change (primary file) | **−89%** |
| Classes / tests | 9 / ~46 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/evals/test_offline_replay.py` | TestOfflineReplay |
| `tests/evals/test_routing_calibration.py` | CalibrationStore + RoutingAdvisorCalibration |
| `tests/evals/test_routing_semantic.py` | TestRoutingAdvisorSemantic |
| `tests/test_evals.py` | shim ≤ 100 LOC |

## Migration order

replay → calibration → semantic → remaining → shim. Move-only.

## Non-goals

Eval harness semantics; landing `main`.
