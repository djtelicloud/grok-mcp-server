# `evals/.../schemas.py` refactor plan (Loop 46)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 558 |
| Projected primary LOC | ~70 facade |
| % LOC change (primary file) | **−87%** |
| Classes / funcs | 21 / 6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/.../schema_envelope.py` | BaseRootEnvelope |
| `evals/.../schema_ttl_receipt.py` | TTLFacts / Receipt |
| `evals/.../schema_json.py` | validate_bounded_json |
| `evals/.../schemas.py` | facade ≤ 70 LOC |

Move-only; envelope validation unchanged.
