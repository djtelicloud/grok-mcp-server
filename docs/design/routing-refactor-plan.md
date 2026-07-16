# `src/routing.py` refactor plan (Loop 85)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 272 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-87%** |
| Hot | `choose_model_candidate` ~99 · `make_routing_receipt` ~27 · `extract_routing_features` ~25 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `choose_model_candidate` |
| split module | concern from hot path `make_routing_receipt` |
| split module | concern from hot path `extract_routing_features` |
| `src/routing.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
