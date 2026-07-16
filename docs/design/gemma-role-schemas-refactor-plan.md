# `evals/.../role_schemas.py` refactor plan (Loop 45)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 559 |
| Projected primary LOC | ~70 facade |
| % LOC change (primary file) | **−87%** |
| Classes / funcs | 12 / 13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/.../role_inputs.py` | FrozenScenarioInput / BlindedCandidate / SeedCandidate |
| `evals/.../role_parse.py` | parse_untrusted_role_payload |
| `evals/.../role_schemas.py` | facade ≤ 70 LOC |

Move-only; untrusted parse stays fail-closed.
