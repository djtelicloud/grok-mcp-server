# `evals/campaigns/gemma_needle_2000_v1/mechanical_mutators.py` refactor plan (Loop 129)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 124 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-72%** |
| Hot | `MechanicalMutators` ~110 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `MechanicalMutators` | extract hot path (~110 LOC) |
| `evals/campaigns/gemma_needle_2000_v1/mechanical_mutators.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
