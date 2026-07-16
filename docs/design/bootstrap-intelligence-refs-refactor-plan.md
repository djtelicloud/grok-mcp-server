# `scripts/bootstrap_intelligence_refs.py` refactor plan (Loop 61)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 435 |
| Projected primary LOC | ~55 facade |
| % LOC change (primary file) | **−87%** |
| Classes / funcs | 1 / 17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `scripts/intel_refs_hash.py` | hash_object |
| `scripts/intel_refs_schema.py` | read_schema_source / validate_schema_anchor |
| `scripts/intel_refs_bootstrap.py` | bootstrap |
| `scripts/bootstrap_intelligence_refs.py` | CLI facade ≤ 55 LOC |

Move-only; no README/public-artifact edits.
