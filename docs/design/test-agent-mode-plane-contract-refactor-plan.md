# `tests/test_agent_mode_plane_contract.py` refactor plan (Loop 103)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 209 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-83%** |
| Hot | `test_discover_self_include_models_exposes_cli_and_api_planes_only` ~82 · `_literal_values` ~20 · `test_using_unigrok_skill_documents_modes_and_planes` ~15 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_discover_self_include_models_exposes_cli_and_api_planes_only` |
| split module | concern from hot path `_literal_values` |
| split module | concern from hot path `test_using_unigrok_skill_documents_modes_and_planes` |
| `tests/test_agent_mode_plane_contract.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
