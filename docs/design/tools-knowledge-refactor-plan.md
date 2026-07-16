# `src/tools/knowledge.py` refactor plan (Loop 125)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 148 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-76%** |
| Hot | `remember_fact` ~30 · `distill_session` ~24 · `search_knowledge` ~23 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `remember_fact` |
| split module | concern from hot path `distill_session` |
| split module | concern from hot path `search_knowledge` |
| `src/tools/knowledge.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
