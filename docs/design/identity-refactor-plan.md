# `src/identity.py` refactor plan (Loop 116)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 165 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-79%** |
| Hot | `scoped_session` ~23 · `principal_kind` ~14 · `resolve_request_caller` ~12 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `scoped_session` |
| split module | concern from hot path `principal_kind` |
| split module | concern from hot path `resolve_request_caller` |
| `src/identity.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
