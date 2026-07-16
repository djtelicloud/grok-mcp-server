# `tests/test_provider_harvest.py` refactor plan (Loop 17)

Status: **Ready for supervisor** — plan only.  
Pairs with: `src/provider_harvest.py`.

## Why not a mega rewrite

**~1188 LOC**, **~30** tests, fakes for collections. Split by harvest lifecycle (credentials, deferral, timeout, document shape).

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1188 |
| Bytes | 44380 |
| Classes / funcs / tests | 2 / 37 / ~30 |
| AST parse / compile | ~5 ms / ~5 ms |
| Branch nodes | 29 |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/provider_harvest/conftest.py` | `FakeCollections` |
| `tests/provider_harvest/test_credentials_pending.py` | missing credentials / pending rows |
| `tests/provider_harvest/test_deferral_corruption.py` | corrupt due-row deferral |
| `tests/provider_harvest/test_timeout_effects.py` | timed-out thread cloud effects |
| `tests/provider_harvest/test_document_shape.py` | deterministic subordinate docs |
| `tests/test_provider_harvest.py` | shim ≤ 100 LOC |

## Migration order

conftest → credentials → deferral → timeout → document → shim. Move-only.

## Risk

Cloud-effect isolation tests — preserve FakeCollections semantics.

## Non-goals

Harvest policy changes; landing `main`.
