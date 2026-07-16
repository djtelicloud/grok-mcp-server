# `src/providers/broker.py` refactor plan (Loop 4)

Status: **Ready for supervisor** — plan only.  
Pairs with: `docs/design/test-provider-broker-refactor-plan.md` (PR #346).  
Lane: Cursor superiority loop.

## Why not a mega rewrite

**~3122 LOC**. Types/helpers occupy ~820 LOC; **`GrokWorkerBroker` alone is ~2302 LOC / 46 methods** — the real split target. Security-critical (grants, replay, projection, cancellation). Extract seams behind a stable `GrokWorkerBroker` facade.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 3122 |
| Bytes | 120432 |
| Classes | 19 |
| Module funcs | 12 |
| `GrokWorkerBroker` methods / span | 46 / ~2302 LOC |
| AST parse / compile | ~9 ms / ~8 ms |
| Branch nodes | 262 |

## Hive

CLI/fast index-diff attempt returned non-answer (skipped once). Structure-driven plan retained; re-poll before first extract PR.

## Swarm

Container env `UNIGROK_SWARM=dry_run` verified. Prefer swarm on **leaf pure helpers** (`_canonical_plan`, digests) after extract — not on full broker class until sliced. IDE Forge MCP may need reconnect after Forge recreate.

## Proposed modules

| Module | Contents | Projected LOC |
|--------|----------|--------------:|
| `src/providers/broker_types.py` | result/plan/evidence dataclasses | 400–550 |
| `src/providers/broker_digests.py` | `_canonical_plan`, lane/execution digests | 200–350 |
| `src/providers/broker_adapters.py` | adapter sources / lifecycle outcomes | 250–400 |
| `src/providers/broker_projection.py` | terminal projection / persistence | 400–700 |
| `src/providers/broker_replay.py` | durable replay / restart safety | 400–700 |
| `src/providers/broker_grants.py` | capability mint / harvest triggers | 300–500 |
| `src/providers/broker.py` | thin `GrokWorkerBroker` facade + re-exports | ≤ 600 |

## Migration order

1. Types + digests (leaf, low behavior risk).
2. Adapter lifecycle helpers.
3. Projection / persistence.
4. Replay + grants last (highest security review).
5. Facade shrink; pair each slice with `tests/providers/` moves.

## Risk

Any semantic drift in replay/grants is a **security defect**. Move-only + existing `tests/test_provider_broker.py` green per slice. No policy changes in extract PRs.

## Non-goals

Changing delegation authority model; landing `main`; swarm-apply on monolith.
