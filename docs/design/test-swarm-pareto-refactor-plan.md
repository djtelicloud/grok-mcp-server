# `tests/test_swarm_pareto.py` refactor plan (Loop 127)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 134 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-74%** |
| Hot | `TestRankCandidates` ~44 · `TestChampionSelection` ~21 · `TestNonDominatedSort` ~17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `TestRankCandidates` | extract hot path (~44 LOC) |
| split / `TestChampionSelection` | extract hot path (~21 LOC) |
| split / `TestNonDominatedSort` | extract hot path (~17 LOC) |
| `tests/test_swarm_pareto.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
