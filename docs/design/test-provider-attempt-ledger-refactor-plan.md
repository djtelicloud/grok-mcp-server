# `tests/test_provider_attempt_ledger.py` refactor plan (Loop 34)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 714 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/provider_ledger/conftest.py` | `_returned` helpers |
| `tests/provider_ledger/test_authority_forgery.py` | forge-clean authority rejects |
| `tests/provider_ledger/test_schema_receipts.py` | started/terminal schema invalid |
| `tests/provider_ledger/test_v15_compat.py` | incompatible preexisting table |
| `tests/test_provider_attempt_ledger.py` | shim ≤ 90 LOC |

Move-only; security-sensitive assertions unchanged.
