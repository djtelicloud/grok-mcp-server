# `src/models/results.py` refactor plan (Loop 152)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 70 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-50%** |
| Hot | `AgentResult` ~33 · `BaseResult` ~15 · `MediaResult` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `AgentResult` | extract hot path (~33 LOC) |
| split / `BaseResult` | extract hot path (~15 LOC) |
| split / `MediaResult` | extract hot path (~6 LOC) |
| `src/models/results.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
