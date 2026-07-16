# `scripts/__init__.py` refactor plan (Loop 197)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 1 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **1900%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `scripts/__init__.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
