# `src/intelligence_capsule.py` refactor plan (Loop 48)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 539 |
| Projected primary LOC | ~70 facade |
| % LOC change (primary file) | **−87%** |
| Funcs | 16 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/intelligence/capsule_parse.py` | parse_canonical |
| `src/intelligence/capsule_envelope.py` | validate_envelope_integrity |
| `src/intelligence/capsule_body.py` | validate_body / _validate_canonical_value |
| `src/intelligence_capsule.py` | facade ≤ 70 LOC |

Move-only; capsule contract unchanged.
