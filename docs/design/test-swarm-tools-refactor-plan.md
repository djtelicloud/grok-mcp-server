# `tests/test_swarm_tools.py` refactor plan (Loop 47)

Status: **Ready for supervisor** — plan only. Pairs with #361 / #385.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 539 |
| Projected primary LOC | ~70 shim |
| % LOC change (primary file) | **−87%** |
| Classes / tests | 3 / ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/swarm/test_gates.py` | TestGates |
| `tests/swarm/test_golden_targets.py` | TestGoldenTargets |
| `tests/swarm/test_e2e.py` | TestEndToEnd |
| `tests/test_swarm_tools.py` | shim ≤ 70 LOC |

Move-only.
