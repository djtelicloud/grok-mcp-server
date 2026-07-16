# `tests/fixtures/swarm_target/test_slow_suite.py` refactor plan (Loop 185)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 9 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **122%** |
| Hot | `test_sorts_slowly` ~3 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_sorts_slowly` | extract hot path (~3 LOC) |
| `tests/fixtures/swarm_target/test_slow_suite.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
