# `src/swarm/fold.py` refactor plan (Loop 147)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 84 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-58%** |
| Hot | `build_folded_state` ~43 · `_reason_key` ~9 · `_dead_end_summary` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `build_folded_state` | extract hot path (~43 LOC) |
| split / `_reason_key` | extract hot path (~9 LOC) |
| split / `_dead_end_summary` | extract hot path (~6 LOC) |
| `src/swarm/fold.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
