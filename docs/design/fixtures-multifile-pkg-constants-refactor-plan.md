# `tests/fixtures/multifile_pkg/constants.py` refactor plan (Loop 189)

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
| `tests/fixtures/multifile_pkg/constants.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
