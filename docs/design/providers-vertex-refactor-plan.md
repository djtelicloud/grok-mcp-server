# `src/providers/vertex.py` refactor plan (Loop 99)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 224 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-84%** |
| Hot | `VertexADCAdapter` ~130 · `load_google_adc_identity` ~43 · `ADCIdentity` ~3 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `VertexADCAdapter` |
| split module | concern from hot path `load_google_adc_identity` |
| split module | concern from hot path `ADCIdentity` |
| `src/providers/vertex.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
