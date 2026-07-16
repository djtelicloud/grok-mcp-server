# `evals/.../attempt_ledger.py` refactor plan (Loop 13)

Status: **Ready for supervisor** — plan only.  
Campaign: `gemma_needle_2000_v1`.

## Why not a mega rewrite

**~1298 LOC**. Errors/types small; **`AttemptLedger` ~936 LOC / 35 methods** is the split core. Keep ledger API stable for harness.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1298 |
| Bytes | 49356 |
| Classes / funcs | 10 / 18 |
| AST parse / compile | ~4 ms / ~4 ms |
| Branch nodes | 95 |
| Hot class | `AttemptLedger` ~936 LOC / 35 methods |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `evals/.../attempt_errors.py` | status + conflict/limit errors | 40–80 |
| `evals/.../attempt_types.py` | ClaimResult, LeaseResult | 40–80 |
| `evals/.../attempt_identity.py` | digests, logical keys, timestamps | 120–180 |
| `evals/.../attempt_ledger_claims.py` | claim/lease transitions | 300–450 |
| `evals/.../attempt_ledger_persist.py` | persistence / artifact fields | 250–400 |
| `evals/.../attempt_ledger.py` | thin `AttemptLedger` facade | ≤ 200 |

## Migration order

errors/types → identity helpers → claims/leases → persist → facade. Pair ledger safety tests.

## Risk

Lease/claim races and terminal transitions — move-only; no semantics change.

## Non-goals

Stage-1 live gen; landing `main`.
