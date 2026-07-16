# `tests/test_campaign.py` refactor plan (Loop 173)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 25 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-20%** |
| Hot | `workspace` ~13 · `test_plan_swarm_campaign` ~3 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `workspace` | extract hot path (~13 LOC) |
| split / `test_plan_swarm_campaign` | extract hot path (~3 LOC) |
| `tests/test_campaign.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
