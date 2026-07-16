# `evals/cassettes.py` refactor plan (Loop 122)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 151 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-77%** |
| Hot | `export_session` ~79 · `stable_substrings` ~23 · `load_cassettes` ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `export_session` |
| split module | concern from hot path `stable_substrings` |
| split module | concern from hot path `load_cassettes` |
| `evals/cassettes.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
