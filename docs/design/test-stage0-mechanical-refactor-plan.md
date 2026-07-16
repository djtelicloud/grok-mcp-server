# `tests/campaigns/gemma_needle_2000_v1/test_stage0_mechanical.py` refactor plan (Loop 106)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 194 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-82%** |
| Hot | `get_valid_base_fixture` ~59 · `test_provider_adapter_strict_validation` ~24 · `test_evaluate_episode_outcome` ~17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `get_valid_base_fixture` |
| split module | concern from hot path `test_provider_adapter_strict_validation` |
| split module | concern from hot path `test_evaluate_episode_outcome` |
| `tests/campaigns/gemma_needle_2000_v1/test_stage0_mechanical.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
