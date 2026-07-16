# `tests/test_swarm_storage.py` refactor plan (Loop 76)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 312 |
| Projected primary LOC | ~40 shim |
| % LOC change (primary file) | **−87%** |
| Classes / tests | 4 / ~20 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/swarm_storage/test_tasks.py` | TestSwarmTasks |
| `tests/swarm_storage/test_candidates.py` | TestSwarmCandidates |
| `tests/swarm_storage/test_config.py` | TestSwarmConfig |
| `tests/test_swarm_storage.py` | shim ≤ 40 LOC |

Move-only. Leave PR #408 alone.
