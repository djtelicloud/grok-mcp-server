# Contributing to UniGrok Public

## Local checks

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
docker compose config --quiet
```

Optional image build (does not start the service):

```bash
docker compose build grok-mcp
```

## Runtime smoke (manual)

With a candidate service on port `4775` (stable may stay on `4765`):

```bash
UNIGROK_PORT=4775 docker compose --env-file .env up --build -d grok-mcp
curl -fsS http://127.0.0.1:4775/healthz
curl -fsS http://127.0.0.1:4775/readyz
```

Exercise MCP from a real IDE pointed at `http://127.0.0.1:4775/mcp`, then compare
`tools/list` with `grok_mcp_discover_self`.

## Pull requests

- Keep changes scoped; match existing style and contracts.
- Do not commit `.env`, OAuth tokens, or API keys.
- Prefer durable job semantics for slow or failure-prone MCP tools: terminal
  success **and** terminal error payloads must persist for `agent_result`.

## Security

Report vulnerabilities via `SECURITY.md`. Do not file public issues for secrets
or unauthenticated remote exposure.
