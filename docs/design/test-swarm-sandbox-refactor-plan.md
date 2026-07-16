# `tests/test_swarm_sandbox.py` refactor plan (Loop 91)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 243 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-86%** |
| Hot | `TestSandbox` ~94 · `TestPreflight` ~46 · `TestParseBenchLine` ~17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `TestSandbox` |
| split module | concern from hot path `TestPreflight` |
| split module | concern from hot path `TestParseBenchLine` |
| `tests/test_swarm_sandbox.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
