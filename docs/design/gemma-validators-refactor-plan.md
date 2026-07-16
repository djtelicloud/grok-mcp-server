# `evals/.../validators.py` refactor plan (Loop 57)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 465 |
| Projected primary LOC | ~50 facade |
| % LOC change (primary file) | **−89%** |
| Hot class | `MechanicalValidators` ~383 LOC |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/.../validator_text.py` | `_iter_text_values` / projection helpers |
| `evals/.../validator_mechanical.py` | MechanicalValidators methods |
| `evals/.../validators.py` | facade ≤ 50 LOC |

Move-only.
