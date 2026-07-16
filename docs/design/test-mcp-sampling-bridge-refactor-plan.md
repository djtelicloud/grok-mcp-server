# `tests/test_mcp_sampling_bridge.py` refactor plan (Loop 12)

Status: **Ready for supervisor** — plan only.  
Pairs with: `src/providers/mcp_sampling.py` / subscription sampling.

## Why not a mega rewrite

**~1379 LOC**, **~29** tests, 1 fake session. Clusters around grants, leases, inflight, auth session. Split by grant/lease concern.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1379 |
| Bytes | 48788 |
| Tests | ~29 |
| AST parse / compile | ~5 ms / ~4 ms |
| Branch nodes | 60 |
| Dense clusters | request_grants, same/two-distinct request, inflight, lease_* |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/mcp_sampling/conftest.py` | `FakeServerSession` |
| `tests/mcp_sampling/test_grants.py` | request_grants, reissued_grant |
| `tests/mcp_sampling/test_leases.py` | lease_and, lease_owned |
| `tests/mcp_sampling/test_inflight_auth.py` | inflight_provider, authenticated_session, outer_task |
| `tests/mcp_sampling/test_sdk_stateful.py` | sdk_results, exact_stateful |
| `tests/test_mcp_sampling_bridge.py` | shim ≤ 120 LOC |

## Migration order

conftest → grants → leases → inflight/auth → sdk → shim. Move-only.

## Risk

Grant/lease identity tests — preserve exact assertion semantics.

## Non-goals

Changing sampling authority; landing `main`.
