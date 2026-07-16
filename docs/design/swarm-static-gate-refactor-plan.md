# `src/swarm/static_gate.py` refactor plan (Loop 140)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 99 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-65%** |
| Hot | `violation_counts` ~49 · `ruff_bin` ~6 · `count_violations` ~4 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `violation_counts` | extract hot path (~49 LOC) |
| split / `ruff_bin` | extract hot path (~6 LOC) |
| split / `count_violations` | extract hot path (~4 LOC) |
| `src/swarm/static_gate.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
