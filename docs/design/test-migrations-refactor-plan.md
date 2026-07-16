# `tests/test_migrations.py` refactor plan (Loop 37)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 696 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−87%** |
| Classes / tests | 5 / ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/migrations/conftest.py` | `_build_v5_db` helpers |
| `tests/migrations/test_chain.py` | TestMigrationChain |
| `tests/migrations/test_v17_cert.py` | TestV17ProviderAttemptCertification |
| `tests/migrations/test_atomicity.py` | TestMigrationFailureAtomicity |
| `tests/test_migrations.py` | shim ≤ 90 LOC |

Move-only.
