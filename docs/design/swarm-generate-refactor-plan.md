# `src/swarm/generate.py` refactor plan (Loop 148)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 83 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-58%** |
| Hot | `generate_mutation` ~56 · `GenerationResult` ~5 · `BudgetExceeded` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `generate_mutation` | extract hot path (~56 LOC) |
| split / `GenerationResult` | extract hot path (~5 LOC) |
| split / `BudgetExceeded` | extract hot path (~2 LOC) |
| `src/swarm/generate.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
