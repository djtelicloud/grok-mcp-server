# `tests/test_multiagent.py` refactor plan (Loop 22)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~1102 LOC**, **15** classes, **~72** tests. Split by multiagent concern (caller derivation, budgets, jobs, metrics).

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1102 |
| Projected primary LOC | ~100 shim |
| % LOC change (primary file) | **−91%** |
| Classes / tests | 15 / ~72 |
| AST parse / compile | measured at plan time |

## Hive / swarm

Forge MCP Not connected — plan path. Swarm retry after IDE reload.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/multiagent/test_http_caller.py` | TestHttpCallerDerivation |
| `tests/multiagent/test_budget_enforcement.py` | TestBudgetEnforcement |
| `tests/multiagent/test_job_caller.py` | TestJobCaller |
| `tests/multiagent/test_metrics_segmentation.py` | TestMetricsSegmentation |
| `tests/multiagent/` + remaining class files | other suites |
| `tests/test_multiagent.py` | shim ≤ 100 LOC |

## Migration order

caller → budgets → jobs → metrics → remaining → shim. Move-only.

## Non-goals

Behavior changes; landing `main`.
