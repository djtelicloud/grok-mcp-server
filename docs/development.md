# Developing UniGrok Public

This guide is for contributors and release verification. Ordinary users only need the
README.

## Local checks

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
docker compose config --quiet
```

## Test beside an existing stable service

Stable may remain on port `4765`; run the candidate on `4775` without changing the
public default:

```bash
UNIGROK_PORT=4775 docker compose --env-file ../.env up --build -d grok-mcp
uv run python scripts/smoke_mcp.py \
  --url http://127.0.0.1:4775/mcp \
  --invoke-cli \
  --invoke-api
```

Verify team-state persistence across a restart:

```bash
uv run python scripts/smoke_team_harness.py --url http://127.0.0.1:4775/mcp
docker compose restart grok-mcp
uv run python scripts/smoke_team_harness.py \
  --url http://127.0.0.1:4775/mcp \
  --verify-existing \
  --cleanup
```

Before release, also compare MCP `tools/list` with `grok_mcp_discover_self`, verify
`/healthz`, `/readyz`, and `/runtimez`, exercise both configured credential planes, and
test from a real IDE opened on an unrelated project.
