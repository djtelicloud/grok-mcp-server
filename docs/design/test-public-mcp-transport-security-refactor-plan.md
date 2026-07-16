# `tests/test_public_mcp_transport_security.py` refactor plan (Loop 175)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 45 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-22%** |
| Hot | `test_public_mcp_transport_security_rejects_link_local_and_internal` ~13 · `test_public_mcp_transport_security_rejects_non_https` ~8 · `test_public_mcp_transport_security_includes_public_hostname` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_public_mcp_transport_security_rejects_link_local_and_internal` | extract hot path (~13 LOC) |
| split / `test_public_mcp_transport_security_rejects_non_https` | extract hot path (~8 LOC) |
| split / `test_public_mcp_transport_security_includes_public_hostname` | extract hot path (~6 LOC) |
| `tests/test_public_mcp_transport_security.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
