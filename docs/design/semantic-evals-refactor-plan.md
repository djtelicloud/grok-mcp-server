# `src/semantic_evals.py` refactor plan (Loop 58)

Status: **Ready for supervisor** — plan only. Pairs with test plan Loop 59.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 463 |
| Projected primary LOC | ~60 facade |
| % LOC change (primary file) | **−87%** |
| Classes / funcs | 3 / 23 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/semantic_evals/stats.py` | _fresh_stats / get_semantic_eval_stats |
| `src/semantic_evals/submit.py` | maybe_submit_semantic_eval |
| `src/semantic_evals/grade.py` | _grade_and_record |
| `src/semantic_evals.py` | facade ≤ 60 LOC |

Move-only.
