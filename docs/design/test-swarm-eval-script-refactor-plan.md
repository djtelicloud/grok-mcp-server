# `tests/test_swarm_eval_script.py` refactor plan (Loop 123)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 150 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-77%** |
| Hot | `test_timeout_requests_cancel_and_returns_partial_payload` ~36 · `test_report_renders_missing_measurements_honestly` ~21 · `test_zero_feasible_is_valid_but_vacuous_cost_or_failed_status_is_not` ~19 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_timeout_requests_cancel_and_returns_partial_payload` |
| split module | concern from hot path `test_report_renders_missing_measurements_honestly` |
| split module | concern from hot path `test_zero_feasible_is_valid_but_vacuous_cost_or_failed_status_is_not` |
| `tests/test_swarm_eval_script.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
