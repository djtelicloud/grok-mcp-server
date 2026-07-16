# `tests/conftest.py` refactor plan (Loop 161)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 42 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-17%** |
| Hot | `reset_global_client` ~14 · `setup_test_env` ~5 · `cleanup_global_store` ~4 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `reset_global_client` | extract hot path (~14 LOC) |
| split / `setup_test_env` | extract hot path (~5 LOC) |
| split / `cleanup_global_store` | extract hot path (~4 LOC) |
| `tests/conftest.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
