# `scripts/land-status.py` refactor plan (Loop 145)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 87 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-60%** |
| Hot | `main` ~52 · `tree_id` ~9 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `main` | extract hot path (~52 LOC) |
| split / `tree_id` | extract hot path (~9 LOC) |
| `scripts/land-status.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
