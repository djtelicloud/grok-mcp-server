# `tests/test_hydration.py` refactor plan (Loop 126)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 137 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-74%** |
| Hot | `FakeHook` ~15 · `test_concurrent_first_use_runs_hook_once` ~12 · `FakeStore` ~11 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `FakeHook` |
| split module | concern from hot path `test_concurrent_first_use_runs_hook_once` |
| split module | concern from hot path `FakeStore` |
| `tests/test_hydration.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
