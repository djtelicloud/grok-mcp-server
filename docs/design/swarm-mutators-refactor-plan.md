# `src/swarm/mutators.py` refactor plan (Loop 117)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 160 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-78%** |
| Hot | `build_mutation_prompt` ~50 · `parse_mutation_output` ~20 · `_boundary_nonce` ~14 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `build_mutation_prompt` |
| split module | concern from hot path `parse_mutation_output` |
| split module | concern from hot path `_boundary_nonce` |
| `src/swarm/mutators.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
