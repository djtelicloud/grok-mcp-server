# `tests/fixtures/swarm_target/test_nocov.py` refactor plan (Loop 194)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 3 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **567%** |
| Hot | `test_unrelated` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_unrelated` | extract hot path (~2 LOC) |
| `tests/fixtures/swarm_target/test_nocov.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
