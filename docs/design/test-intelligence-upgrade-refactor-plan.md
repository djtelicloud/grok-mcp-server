# `tests/test_intelligence_upgrade.py` refactor plan (Loop 50)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 549 |
| Projected primary LOC | ~70 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~28 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/intelligence_upgrade/test_cli_discovery.py` | CLI rejects API-key-backed |
| `tests/intelligence_upgrade/test_list_models.py` | api/cli/profile sections |
| `tests/intelligence_upgrade/test_task_memory.py` | migration/indexes/overlap |
| `tests/test_intelligence_upgrade.py` | shim ≤ 70 LOC |

Move-only.
