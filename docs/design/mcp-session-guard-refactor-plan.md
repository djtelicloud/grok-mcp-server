# `src/mcp_session_guard.py` refactor plan (Loop 19)

Status: **Ready for supervisor** — plan only.  
Pairs with: test plan #354.

## Why not a mega rewrite

**~1105 LOC**. **`StatefulMCPSessionGuard` ~717 LOC** dominates. Extract parse/verify helpers and registry; keep guard facade.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1105 |
| Bytes | 40841 |
| Classes / funcs | 9 / 14 |
| AST parse / compile | ~4 ms / ~3 ms |
| Branch nodes | 144 |
| Hot class | `StatefulMCPSessionGuard` ~717 LOC |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/mcp_session/transport_registry.py` | `MCP128SessionTransportRegistry` | 80–120 |
| `src/mcp_session/initialize_parse.py` | initialize parse/verify helpers | 100–160 |
| `src/mcp_session/ttl_clock.py` | clock/TTL helpers | 60–100 |
| `src/mcp_session/guard.py` | `StatefulMCPSessionGuard` methods | 400–550 |
| `src/mcp_session_guard.py` | re-export facade | ≤ 120 |

## Migration order

registry → parse/verify → TTL → guard methods → facade. Pair #354 test moves.

## Risk

Session binding / cancel races — move-only; green session-guard tests.

## Non-goals

Protocol semantics changes; landing `main`.
