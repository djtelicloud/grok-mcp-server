# `evals/__main__.py` refactor plan (Loop 109)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 193 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-82%** |
| Hot | `main` ~60 · `_cmd_run` ~58 · `_cmd_export_session` ~26 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `main` |
| split module | concern from hot path `_cmd_run` |
| split module | concern from hot path `_cmd_export_session` |
| `evals/__main__.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
