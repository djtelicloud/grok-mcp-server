# `tests/fixtures/multifile_pkg/test_policy.py` refactor plan (Loop 186)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 9 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **122%** |
| Hot | `test_production_timeout` ~2 · `test_development_timeout` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_production_timeout` | extract hot path (~2 LOC) |
| split / `test_development_timeout` | extract hot path (~2 LOC) |
| `tests/fixtures/multifile_pkg/test_policy.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
