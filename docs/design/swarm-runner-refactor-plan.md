# `src/swarm/runner.py` refactor plan (Loop 96)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 228 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `SwarmRunner` ~175 · `is_stale` ~13 · `effective_status` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `SwarmRunner` |
| split module | concern from hot path `is_stale` |
| split module | concern from hot path `effective_status` |
| `src/swarm/runner.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
