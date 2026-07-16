# `src/intelligence_payloads.py` refactor plan (Loop 7)

Status: **Ready for supervisor** — plan only.  
Lane: Cursor superiority loop. Pairs with `tests/test_intelligence_payloads.py` later.

## Why not a mega rewrite

**~1749 LOC**, **0** classes, **33** functions — validators + builders for OptiBench / GNO / DPO / needle tools. Split by payload profile; keep package re-exports stable.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1749 |
| Bytes | 72907 |
| Functions | 33 |
| AST parse / compile | ~8 ms / ~5 ms |
| Branch nodes | 243 |
| Hot funcs | `validate_dpo_preference_graph` ~310; `validate_optibench_evidence` ~172; `build_needle_tools_context` ~157 |

## Hive / swarm

Forge MCP disconnected; plan path. Swarm later on pure validators after extract.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/intelligence/payload_routing.py` | `validate_known_payload_profile`, `_routing` | 80–150 |
| `src/intelligence/optibench.py` | optibench evidence/population validators | 400–550 |
| `src/intelligence/gno.py` | GNO dispatch/result validators | 250–400 |
| `src/intelligence/dpo.py` | DPO preference graph + builders/jsonl | 450–600 |
| `src/intelligence/needle_tools.py` | `build_needle_tools_context` | 150–220 |
| `src/intelligence/pareto_helpers.py` | crowding / nondominated ranks | 100–180 |
| `src/intelligence_payloads.py` | facade re-exports | ≤ 120 |

## Migration order

routing → optibench → gno → pareto helpers → dpo → needle → facade. Green `tests/test_intelligence_payloads.py` per slice.

## Risk

Schema/contract drift vs OKF schemas — move-only; no validation rule changes in extract PRs.

## Non-goals

Changing capsule semantics; landing `main`.
