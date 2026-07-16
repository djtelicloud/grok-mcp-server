# `src/faq.py` refactor plan (Loop 88)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 253 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-86%** |
| Hot | `FAQIndex` ~77 · `parse_faq_document` ~32 · `get_faq_index` ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `FAQIndex` |
| split module | concern from hot path `parse_faq_document` |
| split module | concern from hot path `get_faq_index` |
| `src/faq.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
