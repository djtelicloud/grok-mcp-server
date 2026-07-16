# `src/providers/__init__.py` refactor plan (Loop 114)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 170 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-79%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `src/providers/__init__.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
