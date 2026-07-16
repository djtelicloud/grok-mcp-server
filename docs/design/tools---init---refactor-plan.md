# `src/tools/__init__.py` refactor plan (Loop 195)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 2 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **900%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `src/tools/__init__.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
