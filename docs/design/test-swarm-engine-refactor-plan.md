# `tests/test_swarm_engine.py` refactor plan (Loop 52)

Status: **Ready for supervisor** — plan only. Pairs with #385.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 481 |
| Projected primary LOC | ~60 shim |
| % LOC change (primary file) | **−88%** |
| Classes / tests | 5 / ~27 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/swarm_engine/test_generation_plane.py` | TestGenerationPlane |
| `tests/swarm_engine/test_injection_framing.py` | TestInjectionFraming |
| `tests/swarm_engine/test_fold.py` | TestFold |
| `tests/swarm_engine/test_engine_loop.py` | TestEngineLoop |
| `tests/test_swarm_engine.py` | shim ≤ 60 LOC |

Move-only.
