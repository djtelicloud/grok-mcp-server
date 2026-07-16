# `tests/test_intelligence_refs_bootstrap.py` refactor plan (Loop 66)

Status: **Ready for supervisor** — plan only. Pairs with bootstrap_intelligence_refs plan.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 389 |
| Projected primary LOC | ~50 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~20 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/intel_refs/test_bootstrap_git_local.py` | local git object/ref-only bootstrap |
| `tests/intel_refs/test_schema_anchor.py` | exact blob modes/kinds |
| `tests/intel_refs/test_idempotent_heads.py` | rerun / missing mutable head repair |
| `tests/test_intelligence_refs_bootstrap.py` | shim ≤ 50 LOC |

Move-only; no README/public-artifact edits.
