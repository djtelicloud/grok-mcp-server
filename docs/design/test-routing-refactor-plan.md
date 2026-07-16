# `tests/test_routing.py` refactor plan (Loop 83)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 277 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-87%** |
| Hot | `test_explicit_model_uses_live_catalog_to_resolve_auto_plane` ~24 · `test_live_selector_uses_grok_45_and_emits_reason` ~19 · `test_research_route_selects_multi_agent_capability` ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_explicit_model_uses_live_catalog_to_resolve_auto_plane` |
| split module | concern from hot path `test_live_selector_uses_grok_45_and_emits_reason` |
| split module | concern from hot path `test_research_route_selects_multi_agent_capability` |
| `tests/test_routing.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
