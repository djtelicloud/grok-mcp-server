# `tests/fixtures/multifile_pkg/policy.py` refactor plan (Loop 188)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 7 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **186%** |
| Hot | `timeout_for` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `timeout_for` | extract hot path (~2 LOC) |
| `tests/fixtures/multifile_pkg/policy.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
