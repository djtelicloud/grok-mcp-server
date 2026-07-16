# `tests/test_mcp_session_guard.py` refactor plan (Loop 11)

Status: **Ready for supervisor** — plan only.  
Pairs with: `src/mcp_session_guard.py` (later queue).

## Why not a mega rewrite

**~1397 LOC**, **13** fakes, **~40** tests. Density in TTL/cancel/shutdown races. Split by lifecycle concern; shared fakes in conftest.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1397 |
| Bytes | 48235 |
| Classes (fakes) / tests | 13 / ~40 |
| AST parse / compile | ~6 ms / ~5 ms |
| Branch nodes | 31 |
| Dense clusters | sdk_private, cancelled_shutdown, max_ttl, concurrent/cancel races |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/mcp_session/conftest.py` | FakeClock/Transport/Registry/Revoker/App fakes |
| `tests/mcp_session/test_ttl_idle.py` | idle/max TTL, expired cleanup |
| `tests/mcp_session/test_cancel_shutdown.py` | cancel/shutdown races |
| `tests/mcp_session/test_session_binding.py` | binding / unbound failures |
| `tests/mcp_session/test_sdk_interop.py` | sdk_private / guard interop |
| `tests/test_mcp_session_guard.py` | thin shim ≤ 120 LOC |

## Migration order

conftest → TTL → binding → cancel/shutdown → sdk interop → shim. Move-only.

## Risk

Timing/race tests sensitive to fixture moves — keep FakeClock semantics identical.

## Non-goals

Assertion rewrites; landing `main`.
