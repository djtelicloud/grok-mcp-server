# `evals/runner.py` refactor plan (Loop 39)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 659 |
| Projected primary LOC | ~100 facade |
| % LOC change (primary file) | **−85%** |
| Classes / funcs | 1 / 20 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/runner_task.py` | EvalTask |
| `evals/runner_offline.py` | `_run_one_offline` / tool trace |
| `evals/runner_live.py` | `_run_live_batch` |
| `evals/runner.py` | facade ≤ 100 LOC |

Move-only; no Stage-1 live auth bypass.
