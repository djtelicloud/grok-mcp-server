# `tests/test_intelligence_payloads.py` refactor plan (Loop 14)

Status: **Ready for supervisor** — plan only.  
Pairs with: `docs/design/intelligence-payloads-refactor-plan.md` (#350).

## Why not a mega rewrite

**~1278 LOC**, **~21** top-level tests, no classes. Split by payload profile to match `src/intelligence/*`.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1278 |
| Bytes | 47099 |
| Tests | ~21 |
| AST parse / compile | ~5 ms / ~4 ms |
| Branch nodes | 56 |
| Dense clusters | needle_projection, dpo_*, gno_*, optibench_* |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/intelligence/test_profiles.py` | known_profiles, capsule_v1 |
| `tests/intelligence/test_optibench.py` | optibench evidence/population |
| `tests/intelligence/test_gno.py` | gno_dispatch / rejects |
| `tests/intelligence/test_dpo.py` | dpo_graph/pair/text |
| `tests/intelligence/test_needle.py` | needle_projection |
| `tests/intelligence/test_pareto.py` | exact_nsga2 / crowding |
| `tests/test_intelligence_payloads.py` | shim ≤ 100 LOC |

## Migration order

profiles → optibench → gno → pareto → dpo → needle → shim. Move-only; pair #350 extracts.

## Risk

Schema assertion drift — no assertion edits in move waves.

## Non-goals

Changing OKF contracts; landing `main`.
