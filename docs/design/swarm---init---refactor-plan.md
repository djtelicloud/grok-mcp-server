# `src/swarm/__init__.py` refactor plan (Loop 190)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 7 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **186%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `src/swarm/__init__.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
