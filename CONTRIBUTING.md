# Contributing to UniGrok Public

## Local checks

```bash
uv sync --frozen
uv run ruff check .
bash scripts/ci-insider-denylist.sh
uv run python scripts/check_release_contract.py
uv run python scripts/check_docs.py
uv run pytest -q
docker compose config --quiet
docker compose build grok-mcp
```

## Runtime smoke (manual)

Use an isolated candidate port and state volume when a normal `:4765` instance is
already serving work. The simplest clean-clone smoke is:

```bash
UNIGROK_IMAGE=unigrok:public-candidate UNIGROK_PORT=4775 \
  docker compose up --build -d grok-mcp
curl -fsS http://127.0.0.1:4775/healthz
curl -fsS http://127.0.0.1:4775/readyz
curl -fsS http://127.0.0.1:4775/runtimez
uv run python scripts/smoke_mcp.py --url http://127.0.0.1:4775/mcp
```

Compare MCP `tools/list` with `grok_mcp_discover_self`, then exercise every configured
route. A local smoke does not publish a GitHub release or deploy a hosted service.
Restore the normal port only after the candidate is stopped.

## Pull requests

- Keep changes scoped; match existing style and contracts.
- Keep the tree limited to the generic `README.md` clone/build/start/use
  contract. Private topology, provider sessions, raw evaluations, and operator
  runbooks belong outside this repository.
- Do not commit `.env`, OAuth tokens, or API keys.
- Prefer durable job semantics for slow or failure-prone MCP tools: terminal
  success **and** terminal error payloads must persist for `agent_result`.

## Security

Report vulnerabilities via `SECURITY.md`. Do not file public issues for secrets
or unauthenticated remote exposure.
