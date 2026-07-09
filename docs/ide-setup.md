# UniGrok as a shared local MCP service (multi-IDE setup)

One persistent Docker container serves every IDE on this machine over
streamable HTTP. Verified endpoint: **`http://localhost:8080/mcp`**.

## Start the service

```bash
cd /path/to/uni-grok-mcp        # the real checkout, not a worktree
uv run python main.py init      # first time only; creates .env if absent
# edit .env: set XAI_API_KEY, and optionally UNIGROK_API_KEYS
docker compose up -d --build
curl -s http://localhost:8080/healthz   # -> {"status":"healthy"}
```

Compose loads secrets from the launched checkout's `.env`. Agent worktrees
often do not have that gitignored file; set
`UNIGROK_ENV_FILE=/path/to/.env` before `docker compose up` to use a secret
file from another checkout.

The compose file bind-mounts the repo at `/workspace`, runs that mounted
source with the baked dependency environment, resolves file/git context through
`WORKSPACE_ROOT`, and publishes `127.0.0.1:8080`. Compose declares that this is
a trusted loopback-only host publication so the local prototype can run without
a client token. The application still requires `UNIGROK_API_KEYS` for any
direct non-loopback bind or Cloud Run deployment. Remove the
`UNIGROK_TRUSTED_LOOPBACK_PROXY` declaration and set `UNIGROK_API_KEYS` before
changing the port mapping to `0.0.0.0:8080:8080` or `8080:8080`.

Mount a different project by changing the volume line (`- .:/workspace`)
or pointing compose at that directory. Note: a **git worktree** mounted as
`/workspace` will not resolve git state inside the container (its `.git`
is a pointer into the parent repo outside the mount) — mount the primary
checkout.

## Per-IDE identity: `X-Client-ID`

Every config below sends `X-Client-ID`. It (a) attributes telemetry,
budgets, and `/metrics` per IDE, and (b) namespaces sessions — `vscode`
and `claude-desktop` conversations named `main` stay separate
(`vscode:main` vs `claude-desktop:main`). Omit the header and you share
the plain namespace.

If `UNIGROK_API_KEYS` is set, also add
`"Authorization": "Bearer <one-of-those-keys>"` to each config's headers.

## Claude Code (CLI)

```bash
claude mcp add --transport http unigrok http://localhost:8080/mcp \
  --header "X-Client-ID: claude-code"
```

## Claude Desktop (`claude_desktop_config.json`)

Claude Desktop config-file servers are stdio commands; bridge to HTTP with
`mcp-remote` (or add it as a remote connector in Settings → Connectors):

```json
{
  "mcpServers": {
    "unigrok": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "http://localhost:8080/mcp",
        "--header", "X-Client-ID: claude-desktop"
      ]
    }
  }
}
```

## VS Code (`.vscode/mcp.json` or user `mcp.json`)

```json
{
  "servers": {
    "unigrok": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": { "X-Client-ID": "vscode" }
    }
  }
}
```

## Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.grok]
url = "http://localhost:8080/mcp"
http_headers = { "X-Client-ID" = "codex" }
```

(Field names vary slightly across Codex releases; if `url` isn't accepted,
your version may want the experimental streamable-HTTP client enabled —
check `codex mcp --help`. Keep the server name as `grok`; this repo's
`.codex/mcp/grok-routing.json` and Codex intelligence config route to the
`mcp__grok` tool namespace.)

## Cursor (`.cursor/mcp.json` in project root, or `~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "unigrok": {
      "url": "http://localhost:8080/mcp",
      "name": "UniGrok MCP Gateway",
      "description": "Shared Grok agent with live Control Center, cost tracking, reasoning guard, OKF + WebMCP self-discovery",
      "headers": { "X-Client-ID": "cursor" }
    }
  }
}
```

Cursor auto-detects HTTP servers from the `url` field. After saving, enable
the server under Settings → MCP; the `agent` tool appears in Composer/chat.

## Antigravity / Gemini (`settings.json` → MCP servers)

```json
{
  "mcpServers": {
    "unigrok": {
      "httpUrl": "http://localhost:8080/mcp",
      "headers": { "X-Client-ID": "antigravity" }
    }
  }
}
```

## What the IDEs get

The public MCP surface is the single `agent` tool (modes:
auto/fast/reasoning/thinking/research) — UniGrok routes across Grok models,
runs xAI server-side tools plus workspace file/git tools against
`/workspace`, and remembers per-client sessions. `/metrics` (JSON or
`?format=prometheus`) shows per-caller usage.

`agent` returns `response` plus execution metadata: `route`, `plane`, `model`,
`why` (`pin`, `cost`, `auto`, or `failover`), `degraded`, `profile`,
`finish_reason`, token/cost totals, latency, and citations when upstream
provides them. `degraded=true` means the run fell back from the initially
selected route or plane.

The local CLI plane works **inside the container**: the image bakes the
Linux `grok` binary (version-pinned in the Dockerfile) and compose mounts the
host's `~/.grok` (your `grok login` OAuth session, refresh token included) at
the runtime user's home. The startup log reports both binary and auth-state
availability. Requests that pin a CLI model (`grok-build`,
`grok-composer-2.5-fast`) run on the grok.com subscription at $0 marginal
cost; API-plane failures degrade to the CLI plane instead of failing.
Remove the `~/.grok` volume in `docker-compose.yml` for an API-only container.
