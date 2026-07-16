# `tests/test_workspace_memory.py` refactor plan (Loop 70)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 365 |
| Projected primary LOC | ~45 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/workspace_memory/test_record_dedupe.py` | record dedupe / git note mirror |
| `tests/workspace_memory/test_recall_scope.py` | ancestry / supersession / changed-deleted |
| `tests/workspace_memory/test_concurrent_notes.py` | concurrent note writers |
| `tests/test_workspace_memory.py` | shim ≤ 45 LOC |

Move-only.
