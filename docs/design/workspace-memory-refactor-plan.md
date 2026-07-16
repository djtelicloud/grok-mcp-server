# `src/workspace_memory.py` refactor plan (Loop 38)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 668 |
| Projected primary LOC | ~100 facade |
| % LOC change (primary file) | **−85%** |
| Classes / funcs | 1 / 23 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/workspace_memory/git_notes.py` | import/write git notes |
| `src/workspace_memory/landed.py` | record_landed_outcome |
| `src/workspace_memory/recall.py` | recall_workspace_memory |
| `src/workspace_memory.py` | facade ≤ 100 LOC |

Move-only; repo fence semantics unchanged.
