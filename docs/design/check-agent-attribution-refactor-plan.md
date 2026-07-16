# `scripts/check_agent_attribution.py` refactor plan (Loop 69)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 369 |
| Projected primary LOC | ~45 facade |
| % LOC change (primary file) | **−88%** |
| Hot | `load_registry` ~112 · `validate_agent_credit` ~41 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `scripts/attribution_registry.py` | load_registry |
| `scripts/attribution_credit.py` | validate_agent_credit |
| `scripts/attribution_commit.py` | _validate_commit / validate_commit_range |
| `scripts/check_agent_attribution.py` | CLI facade ≤ 45 LOC |

Move-only; trailer rules unchanged.
