# `evals/graders.py` refactor plan (Loop 138)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 102 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-66%** |
| Hot | `_grade_one` ~44 · `run_graders` ~17 · `_normalize` ~10 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `_grade_one` | extract hot path (~44 LOC) |
| split / `run_graders` | extract hot path (~17 LOC) |
| split / `_normalize` | extract hot path (~10 LOC) |
| `evals/graders.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
