# `tests/fixtures/swarm_target/test_slow.py` refactor plan (Loop 178)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 16 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **25%** |
| Hot | `test_does_not_mutate_input` ~4 · `test_empty_and_duplicates` ~3 · `test_sorts` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_does_not_mutate_input` | extract hot path (~4 LOC) |
| split / `test_empty_and_duplicates` | extract hot path (~3 LOC) |
| split / `test_sorts` | extract hot path (~2 LOC) |
| `tests/fixtures/swarm_target/test_slow.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
