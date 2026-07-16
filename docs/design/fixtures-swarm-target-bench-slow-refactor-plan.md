# `tests/fixtures/swarm_target/bench_slow.py` refactor plan (Loop 183)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 10 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **100%** |
| Hot | n/a |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | domain seams |
| `tests/fixtures/swarm_target/bench_slow.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
