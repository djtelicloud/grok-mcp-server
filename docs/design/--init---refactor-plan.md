# `src/__init__.py` refactor plan (Loop 187)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 9 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **122%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `src/__init__.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
