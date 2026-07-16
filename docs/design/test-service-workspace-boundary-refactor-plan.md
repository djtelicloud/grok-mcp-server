# `tests/test_service_workspace_boundary.py` refactor plan (Loop 44)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 580 |
| Projected primary LOC | ~80 shim |
| % LOC change (primary file) | **−86%** |
| Tests | ~28 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/boundary/test_compose_separation.py` | stable vs contributor compose |
| `tests/boundary/test_discover_bootstrap.py` | discover_self / cloudrun surface |
| `tests/boundary/test_review_courier.py` | untrusted evidence courier |
| `tests/test_service_workspace_boundary.py` | shim ≤ 80 LOC |

Move-only; boundary assertions fail-closed.
