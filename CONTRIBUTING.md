# Contributing to UniGrok Public

## Local checks

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
docker compose config --quiet
docker compose build grok-mcp
```

## Runtime smoke (manual)

Compose uses one fixed container plus persistent auth/state volumes; it is not safe to
run a second project against those same volumes. Stop and recreate the current local
service on `4775` for a candidate smoke:

```bash
docker compose stop grok-mcp
UNIGROK_PORT=4775 docker compose --env-file .env up --build -d grok-mcp
curl -fsS http://127.0.0.1:4775/healthz
curl -fsS http://127.0.0.1:4775/readyz
curl -fsS http://127.0.0.1:4775/runtimez
uv run python scripts/smoke_mcp.py --url http://127.0.0.1:4775/mcp
```

Compare MCP `tools/list` with `grok_mcp_discover_self`, then exercise both configured
credential planes. Restore the normal port by recreating this same service on `4765`;
do not point two containers at one state volume.

This local smoke does not deploy the authenticated hosted service. Maintainer-operated
remote releases follow the digest, OAuth, public-smoke, and rollback gates in
[`docs/remote-mcp-deployment.md`](docs/remote-mcp-deployment.md).

## Pull requests

- Keep changes scoped; match existing style and contracts.
- Do not commit `.env`, OAuth tokens, or API keys.
- Prefer durable job semantics for slow or failure-prone MCP tools: terminal
  success **and** terminal error payloads must persist for `agent_result`.

## Security

Report vulnerabilities via `SECURITY.md`. Do not file public issues for secrets
or unauthenticated remote exposure.
