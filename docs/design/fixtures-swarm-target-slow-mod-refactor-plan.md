# `tests/fixtures/swarm_target/slow_mod.py` refactor plan (Loop 168)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 34 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-41%** |
| Hot | `slow_sort` ~9 · `Widget` ~6 · `outer` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `slow_sort` | extract hot path (~9 LOC) |
| split / `Widget` | extract hot path (~6 LOC) |
| split / `outer` | extract hot path (~5 LOC) |
| `tests/fixtures/swarm_target/slow_mod.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
