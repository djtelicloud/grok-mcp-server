# `src/cli.py` refactor plan (Loop 111)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 268 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-87%** |
| Hot | `_write_init_config` ~54 · `main` ~43 · `init_project` ~28 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `_write_init_config` |
| split module | concern from hot path `main` |
| split module | concern from hot path `init_project` |
| `src/cli.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
