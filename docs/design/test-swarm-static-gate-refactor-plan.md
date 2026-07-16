# `tests/test_swarm_static_gate.py` refactor plan (Loop 89)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 250 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-86%** |
| Hot | `TestFunnelGate` ~118 · `TestCountViolations` ~37 · `_make_engine` ~20 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `TestFunnelGate` |
| split module | concern from hot path `TestCountViolations` |
| split module | concern from hot path `_make_engine` |
| `tests/test_swarm_static_gate.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
