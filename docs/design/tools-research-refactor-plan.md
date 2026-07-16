# `src/tools/research.py` refactor plan (Loop 146)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 108 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-68%** |
| Hot | `submit_research_job` ~34 · `get_research_job` ~23 · `list_research_jobs` ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `submit_research_job` | extract hot path (~34 LOC) |
| split / `get_research_job` | extract hot path (~23 LOC) |
| split / `list_research_jobs` | extract hot path (~13 LOC) |
| `src/tools/research.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
