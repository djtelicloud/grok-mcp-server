# `src/completion_envelope.py` refactor plan (Loop 31)

Status: **Ready for supervisor** — plan only.  
Pairs later with `tests/test_completion_envelope.py`.

## Why not a mega rewrite

**~791 LOC**, **11** classes. Hot validators: schema shape, json boundary, unwrap, evidence refs. Split validate vs unwrap.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 791 |
| Projected primary LOC | ~100 facade |
| % LOC change (primary file) | **−87%** |
| Classes / funcs | 11 / 14 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/completion_envelope/schema.py` | caller schema validation | 120–160 |
| `src/completion_envelope/json_boundary.py` | `_json_boundary` | 80–120 |
| `src/completion_envelope/evidence.py` | validate_evidence_refs | 60–100 |
| `src/completion_envelope/unwrap.py` | unwrap_complete_result | 80–120 |
| `src/completion_envelope.py` | facade | ≤ 100 |

## Migration order

schema → json_boundary → evidence → unwrap → facade. Move-only.

## Non-goals

Envelope contract changes; landing `main`.
