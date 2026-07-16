# `tests/test_swarm_runner.py` refactor plan (Loop 162)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 40 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-50%** |
| Hot | `TestStaleness` ~24 · `_row` ~3 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `TestStaleness` | extract hot path (~24 LOC) |
| split / `_row` | extract hot path (~3 LOC) |
| `tests/test_swarm_runner.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
