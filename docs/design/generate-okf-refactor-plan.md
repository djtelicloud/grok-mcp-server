# `scripts/generate_okf.py` refactor plan (Loop 92)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 240 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `render_api_reference` ~47 · `extract_docs_from_file` ~31 · `check_bundle` ~30 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `render_api_reference` |
| split module | concern from hot path `extract_docs_from_file` |
| split module | concern from hot path `check_bundle` |
| `scripts/generate_okf.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
