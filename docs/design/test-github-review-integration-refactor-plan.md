# `tests/test_github_review_integration.py` refactor plan (Loop 59)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 484 |
| Projected primary LOC | ~60 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/github_review/test_compare_race.py` | immutable compare A-B-A |
| `tests/github_review/test_head_base_reject.py` | head/base change before comment |
| `tests/github_review/test_stale_event.py` | already-stale event head |
| `tests/test_github_review_integration.py` | shim ≤ 60 LOC |

Move-only; race assertions unchanged.
