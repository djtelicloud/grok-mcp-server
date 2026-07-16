# `tests/test_intelligence_capsule.py` refactor plan (Loop 105)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 200 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-82%** |
| Hot | `test_published_schema_is_strict_and_versioned` ~49 · `test_python_rejects_primitive_subclasses_before_serialization` ~20 · `test_builder_recomputes_digest_without_changing_body_identity` ~14 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_published_schema_is_strict_and_versioned` |
| split module | concern from hot path `test_python_rejects_primitive_subclasses_before_serialization` |
| split module | concern from hot path `test_builder_recomputes_digest_without_changing_body_identity` |
| `tests/test_intelligence_capsule.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
