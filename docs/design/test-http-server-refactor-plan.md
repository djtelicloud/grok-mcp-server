# `tests/test_http_server.py` refactor plan (Loop 6)

Status: **Ready for supervisor** — plan only.  
Pairs with: `docs/design/http-server-refactor-plan.md` (#348).

## Why not a mega rewrite

**~2371 LOC**, **~105** tests, mostly top-level functions + 2 fakes. Split by HTTP surface to match `src/http/*` extracts; move-only first.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 2371 |
| Bytes | 87692 |
| Tests | ~105 |
| Classes (fakes) | 2 |
| AST parse / compile | ~10 ms / ~7 ms |
| Branch nodes | 91 |
| Dense clusters | agent_stream/chat, oauth_*, post_xai /v1, metrics/runtimez |

## Hive / swarm

Forge MCP disconnected; plan path. Pair moves with #348 modules.

## Proposed modules

| File | Cluster |
|------|---------|
| `tests/http/conftest.py` | `FakeStreamResponse`, `FakeAsyncClient` |
| `tests/http/test_health_runtimez.py` | health/readyz/runtimez |
| `tests/http/test_metrics.py` | metrics aggregates |
| `tests/http/test_oauth_gateway.py` | oauth introspection/metadata/mcp |
| `tests/http/test_v1_openai.py` | post_xai, xai_chat, streams |
| `tests/http/test_public_agent_mcp.py` | public_agent, MCP transport |
| `tests/http/test_origin_auth.py` | credential-bearing / origin guards |
| `tests/test_http_server.py` | thin shim ≤ 150 LOC |

## Migration order

conftest → health/metrics → oauth → /v1 → MCP/agent → shrink shim. Match #348 extract order.

## Risk

Auth/Origin/OAuth flakes — one cluster per PR; keep green collection via shim.

## Non-goals

Assertion rewrites; landing `main`.
