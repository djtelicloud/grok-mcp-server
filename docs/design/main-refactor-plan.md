# `main.py` refactor plan (Loop 193)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 5 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **300%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `main.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
