# `tests/fixtures/multifile_pkg/__init__.py` refactor plan (Loop 192)

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
| `tests/fixtures/multifile_pkg/__init__.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
