# `src/swarm/engine.py` refactor plan (Loop 42)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 570 |
| Projected primary LOC | ~80 facade |
| % LOC change (primary file) | **−86%** |
| Hot class | `SwarmEngine` ~475 LOC |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/swarm/engine_config.py` | EngineConfig / GenerationOutcome |
| `src/swarm/engine_core.py` | SwarmEngine methods |
| `src/swarm/engine_diff.py` | `_byte_diff_size` helpers |
| `src/swarm/engine.py` | facade ≤ 80 LOC |

Move-only; Pareto/eval semantics unchanged.
