# `tests/test_phase4.py` refactor plan (Loop 135)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 114 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-69%** |
| Hot | `test_database_migration_and_indexes` ~21 · `test_sync_history_helpers_metadata` ~18 · `test_message_metadata_roundtrip` ~17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_database_migration_and_indexes` | extract hot path (~21 LOC) |
| split / `test_sync_history_helpers_metadata` | extract hot path (~18 LOC) |
| split / `test_message_metadata_roundtrip` | extract hot path (~17 LOC) |
| `tests/test_phase4.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
