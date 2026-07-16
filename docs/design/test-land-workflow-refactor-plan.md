# `tests/test_land_workflow.py` refactor plan (Loop 73)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 388 |
| Projected primary LOC | ~45 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/land/test_preflight.py` | attribution / OKF before pytest |
| `tests/land/test_main_race.py` | main advances during tests |
| `tests/land/test_runtime_reconcile.py` | failed reconcile retry / restart handoff |
| `tests/test_land_workflow.py` | shim ≤ 45 LOC |

Move-only; land gates unchanged. Plan-only — does not alter `scripts/land.py` behavior.
