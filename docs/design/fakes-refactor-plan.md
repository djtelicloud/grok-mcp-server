# `evals/fakes.py` refactor plan (Loop 93)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 235 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `FakeChat` ~64 · `FakeClient` ~45 · `response_from_spec` ~29 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `FakeChat` |
| split module | concern from hot path `FakeClient` |
| split module | concern from hot path `response_from_spec` |
| `evals/fakes.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
