# `src/providers/mcp_sampling.py` refactor plan (Loop 24)

Status: **Ready for supervisor** — plan only.  
Pairs with: sampling bridge tests #355 / subscription #353.

## Why not a mega rewrite

**~986 LOC**. **`StatefulMCPSamplingLease` ~389 LOC** + factory `create_stateful_mcp_sampling_lease` ~152. Split lease/runtime/capability.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 986 |
| Projected primary LOC | ~120 facade |
| % LOC change (primary file) | **−88%** |
| Classes / funcs | 5 / 5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/providers/mcp_sampling_capability.py` | TrustedMCPProviderCapability | 80–120 |
| `src/providers/mcp_sampling_runtime.py` | MCPSamplingSessionRuntime | 120–180 |
| `src/providers/mcp_sampling_lease.py` | StatefulMCPSamplingLease | 350–450 |
| `src/providers/mcp_sampling_factory.py` | create_stateful_mcp_sampling_lease | 150–200 |
| `src/providers/mcp_sampling.py` | facade | ≤ 120 |

## Migration order

capability → runtime → lease → factory → facade. Security-sensitive move-only.

## Non-goals

Grant/lease semantics changes; landing `main`.
