# `tests/test_semantic_evals.py` refactor plan (Loop 60)

Status: **Ready for supervisor** — plan only. Pairs with # semantic_evals plan.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 441 |
| Projected primary LOC | ~55 shim |
| % LOC change (primary file) | **−88%** |
| Classes / tests | 5 / ~24 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/semantic_evals/test_mode_gates.py` | TestModeAndGates |
| `tests/semantic_evals/test_grade_record.py` | TestGradeAndRecord |
| `tests/semantic_evals/test_agent_trigger.py` | TestRunAgentTurnTrigger |
| `tests/semantic_evals/test_attach_scores.py` | TestAttachSemanticScores |
| `tests/test_semantic_evals.py` | shim ≤ 55 LOC |

Move-only.
