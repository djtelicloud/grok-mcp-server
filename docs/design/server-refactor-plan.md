# `src/server.py` refactor plan (Loop 113)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 171 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-80%** |
| Hot | `main` ~14 · `server_lifespan` ~7 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `main` |
| split module | concern from hot path `server_lifespan` |
| `src/server.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
