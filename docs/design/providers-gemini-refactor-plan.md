# `src/providers/gemini.py` refactor plan (Loop 131)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 119 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-71%** |
| Hot | `GeminiAdapter` ~89 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `GeminiAdapter` | extract hot path (~89 LOC) |
| `src/providers/gemini.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
