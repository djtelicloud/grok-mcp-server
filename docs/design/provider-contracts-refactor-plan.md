# `src/providers/contracts.py` refactor plan (Loop 27)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~877 LOC**, **21** dataclasses/types. Split by contract family (binding, descriptor, receipt, failure).

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 877 |
| Projected primary LOC | ~100 facade |
| % LOC change (primary file) | **−89%** |
| Classes / funcs | 21 / 7 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/providers/contract_binding.py` | ProviderExecutionBinding |
| `src/providers/contract_descriptor.py` | ProviderDescriptor |
| `src/providers/contract_receipt.py` | ProviderReceipt / FailureReceipt |
| `src/providers/contracts.py` | facade re-exports ≤ 100 LOC |

## Migration order

binding → descriptor → receipts → facade. Move-only; no schema semantics change.

## Non-goals

Landing `main`.
