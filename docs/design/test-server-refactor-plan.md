# `tests/test_server.py` refactor plan (Loop 20)

Status: **Ready for supervisor** — plan only.  
Pairs with: system tools plan #352 / server registration.

## Why not a mega rewrite

**~1110 LOC**, **~52** top-level tests. Split by MCP tool surface (status, discover, agent, restart).

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1110 |
| Bytes | 43090 |
| Tests | ~52 |
| AST parse / compile | ~4 ms / ~4 ms |
| Branch nodes | 42 |
| Dense | restart gating, discover catalog, agent metadata, status json |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/server/test_status.py` | grok_mcp_status views |
| `tests/server/test_discover.py` | discover_self / catalog |
| `tests/server/test_agent_tool.py` | agent structured metadata |
| `tests/server/test_restart_gate.py` | restart container gating |
| `tests/server/test_reflect_misc.py` | reflect + remaining tools |
| `tests/test_server.py` | shim ≤ 100 LOC |

## Migration order

status → discover → agent → restart → misc → shim. Move-only.

## Risk

Plane-specific discover assertions — keep exact.

## Non-goals

Tool schema changes; landing `main`.
