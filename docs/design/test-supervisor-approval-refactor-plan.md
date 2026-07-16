# `tests/test_supervisor_approval.py` refactor plan (Loop 77)

Status: **Ready for supervisor** — plan only. Pairs with supervisor_approval plan.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 326 |
| Projected primary LOC | ~40 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~21 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/supervisor/test_check_states.py` | collect_check_states preference races |
| `tests/supervisor/test_bugbot_security_pending.py` | bugbot neutral / missing security |
| `tests/test_supervisor_approval.py` | shim ≤ 40 LOC |

Move-only.
