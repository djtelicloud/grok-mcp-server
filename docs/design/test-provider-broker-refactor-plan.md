# `tests/test_provider_broker.py` refactor plan (Loop 3)

Status: **Ready for supervisor** — plan only.  
Pairs with later: `src/providers/broker.py` (~3.1k LOC, Loop 4 queue).  
Lane: Cursor superiority loop.

## Why not a mega rewrite

**~4707 LOC**, **~95–101** top-level tests, few helper fakes (`FakeAdapter`, `FakeStore`, …). Density is in long security/correctness tests (grants, replay, cancellation, descriptor spoofing). Split by **concern cluster**, move-only first.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 4707 |
| Bytes | 163008 |
| Test functions | ~95–101 |
| Helper classes | 5 |
| AST parse / compile | ~17 ms / ~14 ms |
| Branch nodes | 106 |

## Hive

CLI · fast · same_plane · `grok-4.5` · $0 — L1/L3–L6 GOOD; L2 KEEP (pair with broker extract).

## Swarm

Forge MCP reconnect may be needed after `UNIGROK_SWARM=dry_run` recreate. Plan path continues regardless.

## Proposed modules

| New file | Concern cluster | Notes |
|----------|-----------------|-------|
| `tests/providers/conftest.py` | fakes + `_plan` / `_forged_attempt` helpers | shared |
| `tests/providers/test_broker_plan_registry.py` | plan content-addressing, registry spoof | medium |
| `tests/providers/test_broker_projection.py` | projection, secret rotation, blocking | high risk |
| `tests/providers/test_broker_cancellation.py` | cancel / cleanup / terminal persistence | high risk |
| `tests/providers/test_broker_replay.py` | durable replay, restart forgery | high risk |
| `tests/providers/test_broker_mcp_claims.py` | claimed MCP / descriptor switches | isolate |
| `tests/providers/test_broker_harvest_grants.py` | capability grants, harvester TTL | isolate |
| `tests/test_provider_broker.py` | thin shim / collection alias | ≤ 150 LOC |

## Migration order

1. Extract helpers → `conftest.py` / `fakes.py` (no behavior change).
2. Move lowest-coupling plan/registry tests.
3. Projection + cancellation clusters (paired review).
4. Replay + MCP claim + harvest last.
5. Shrink shim; pair code moves with `src/providers/broker.py` plan/extract.

## Risk

High-risk auth/failover/replay tests must stay isolated and green per PR. No assertion rewrites in move-only waves.

## Non-goals

Mega rewrite; changing broker security semantics; landing `main`.
