# `src/http_server.py` refactor plan (Loop 5)

Status: **Ready for supervisor** — plan only.  
Lane: Cursor superiority loop. Pairs with `tests/test_http_server.py` (next).

## Why not a mega rewrite

**~2874 LOC**, **9** middleware/result classes, **~100** functions. Surfaces mix MCP, `/v1`, health/metrics, UI/static, auth. Split by surface; keep `create_app` / `create_public_mcp` as thin facades.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 2874 |
| Bytes | 111981 |
| Classes / funcs | 9 / 100 |
| AST parse / compile | ~11 ms / ~9 ms |
| Branch nodes | 239 |
| Hot spans | `_render_prometheus_metrics` ~245; `public_agent` ~117; `create_public_mcp` ~88; `create_app` ~78 |

## Hive / swarm

Forge MCP **Not connected** (tried once). Stable CLI hive returned non-answer (skipped). Structure plan retained. Swarm not run (MCP down); container previously `UNIGROK_SWARM=dry_run`.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/http/middleware.py` | Origin, auth, caller, request-id, body limit, CSP, cache | 350–500 |
| `src/http/health.py` | `/healthz`, `/readyz`, `/runtimez`, discovery | 200–350 |
| `src/http/metrics.py` | `/metrics` + prometheus render | 250–400 |
| `src/http/v1_openai.py` | OpenAI-compat chat/stream `/v1` | 400–600 |
| `src/http/mcp_http.py` | public MCP transport + security | 300–500 |
| `src/http/control_ui.py` | Control Center / static UI routes | 300–500 |
| `src/http/oauth_gateway.py` | API key records, OAuth introspect | 200–350 |
| `src/http_server.py` | `create_app` / wiring facade | ≤ 400 |

## Migration order

1. Middleware + health (lowest coupling).
2. Metrics render.
3. OAuth/gateway helpers.
4. `/v1` then MCP HTTP.
5. UI/static last; shrink facade; pair `tests/test_http_server.py` moves.

## Risk

Auth/Origin/CSRF regressions — move-only; green `tests/test_http_server.py` per slice. No public MCP schema changes.

## Non-goals

Mega rewrite; changing dual-plane policy; landing `main`.
