# `tests/.../test_attempt_ledger_safety.py` refactor plan (Loop 41)

Status: **Ready for supervisor** — plan only. Pairs with attempt_ledger #356.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 612 |
| Projected primary LOC | ~80 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~19 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/campaigns/.../ledger/test_limits_concurrency.py` | atomic claim / limits |
| `tests/campaigns/.../ledger/test_contract_drift.py` | persisted contract drift |
| `tests/campaigns/.../ledger/test_completion_artifacts.py` | digest/provenance |
| thin shim at current path | ≤ 80 LOC |

Move-only; concurrency assertions unchanged.
