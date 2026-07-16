# `src/swarm/ast_utils.py` refactor plan (Loop 101)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 213 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-84%** |
| Hot | `signature_fingerprint` ~46 · `extract_node_span` ~42 · `_functions_in` ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `signature_fingerprint` |
| split module | concern from hot path `extract_node_span` |
| split module | concern from hot path `_functions_in` |
| `src/swarm/ast_utils.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
