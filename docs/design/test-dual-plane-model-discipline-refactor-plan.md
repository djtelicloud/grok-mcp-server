# `tests/test_dual_plane_model_discipline.py` refactor plan (Loop 110)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 186 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-81%** |
| Hot | `test_build_model_catalog_shape_is_dual_xai_planes_only` ~43 · `test_multi_provider_package_stays_inert_on_public_surfaces` ~16 · `test_credential_plane_contract_is_xai_api_and_cli_only` ~15 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_build_model_catalog_shape_is_dual_xai_planes_only` |
| split module | concern from hot path `test_multi_provider_package_stays_inert_on_public_surfaces` |
| split module | concern from hot path `test_credential_plane_contract_is_xai_api_and_cli_only` |
| `tests/test_dual_plane_model_discipline.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
