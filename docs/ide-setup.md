# UniGrok as a shared local MCP service (multi-IDE setup)

One persistent Docker container serves every IDE on this machine over
streamable HTTP. Verified endpoint: **`http://localhost:4765/mcp`**. The port
spells **GROK** on a telephone keypad.

If you are new to local developer tools: install Git, Docker Desktop, and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/) first. The
host-facing MCP, health, and Control Center URLs always use port `4765`;
`8080` is container-internal and should not appear in an IDE config.

## Start the service

```bash
cd /path/to/uni-grok-mcp        # the real checkout, not a worktree
uv run python main.py init      # first time only; creates .env if absent
docker compose up -d --build
curl -s http://localhost:4765/healthz   # -> {"status":"healthy"}
```

Choose at least one credential path:

- **SuperGrok subscription:** run `docker compose run --rm grok-cli-auth` and
  complete the device-code login. No `XAI_API_KEY` is required for compatible
  CLI-plane work.
- **xAI developer API:** edit `.env` and replace the `XAI_API_KEY` placeholder.
- **Both:** recommended for maximum coverage. The default `cli_first` policy
  prefers compatible subscription work while preserving API-only features.

Open `http://localhost:4765/ui/` and use **Setup & Status** to confirm which
credential planes are ready before configuring every IDE.

Compose loads secrets from the launched checkout's `.env`. Agent worktrees
often do not have that gitignored file; set
`UNIGROK_ENV_FILE=/path/to/.env` before `docker compose up` to use a secret
file from another checkout.

The stable compose file runs the image's baked application at `/app`, stores
mutable data in a Docker volume mounted at `/state`, and publishes
`127.0.0.1:4765`. It does not mount the UniGrok checkout or an IDE project.
Compose declares that this is a trusted loopback-only host publication so the
local service can run without a client token. The application still requires `UNIGROK_API_KEYS` for any
direct non-loopback bind or Cloud Run deployment. Remove the
`UNIGROK_TRUSTED_LOOPBACK_PROXY` declaration and set `UNIGROK_API_KEYS` before
changing the port mapping to `0.0.0.0:4765:8080` or `4765:8080`.

Do not edit Compose to mount each project you open. MCP registration is global
service access, not filesystem authority. A calling IDE supplies only the
material Grok needs through the optional `agent.workspace_context` field.
Projects therefore need no UniGrok-specific namespace folders.

UniGrok contributors have a separate live-source “Forge” service at port 4766:

```bash
# Edit .env first: UNIGROK_SWARM=dry_run
docker compose -f docker-compose.dev.yml up --build -d
curl -s http://localhost:4766/runtimez
```

That contributor service mounts this repository at `/workspace` and enables
local file/git/test, commit-memory, and Code Swarm facilities. The Compose file
already sets `UNIGROK_CONTRIBUTOR_MODE=1` and `WORKSPACE_ROOT=/workspace`; do
not invent a second workspace variable or copy those values into every IDE.
Compose reads model credentials from the server-side `.env`, so never paste
`XAI_API_KEY` into an IDE MCP configuration. Code Swarm generation requires the
container's Grok CLI plane to report `ready` because it is pinned to the
subscription plane and cannot cross to metered API fallback.

Register `http://localhost:4766/mcp` as a separate, repository-specific MCP
entry only while developing UniGrok. Keep the global stable entry on `4765` for
unrelated projects. The contributor Control Center is
`http://localhost:4766/ui/`, and the Pareto Playground is
`http://localhost:4766/ui/swarm.html`.

## Optional Grok Dial Plan

Power users can add phoneword mode defaults without running more UniGrok
instances:

```bash
docker compose -f docker-compose.yml -f docker-compose.dials.yml up --build -d
```

The overlay publishes `AUTO=2886`, `FAST=3278`, `REAS=7327`, `THNK=8465`, and
`RSCH=7724`. Each port maps to the same stable container, state, sessions, and
authentication. The incoming `Host` port becomes the default `agent` mode only
when the caller omits `mode`; an explicit mode always wins. Keep ordinary IDE
registration on `4765` unless a particular IDE should always begin in one mode.

## Per-IDE identity: `X-Client-ID`

Every config below sends `X-Client-ID`. It (a) attributes telemetry,
budgets, and `/metrics` per IDE, and (b) namespaces sessions — `vscode`
and `claude-desktop` conversations named `main` stay separate
(`vscode:main` vs `claude-desktop:main`). Omit the header and you share
the plain namespace.

If `UNIGROK_API_KEYS` is set, also add
`"Authorization": "Bearer <one-of-those-keys>"` to each config's headers.

## Cursor (`.cursor/mcp.json` in project root, or `~/.cursor/mcp.json`)

With Cursor joining the xAI family, it's the natural first-class Grok IDE.

```json
{
  "mcpServers": {
    "unigrok": {
      "url": "http://localhost:4765/mcp",
      "name": "UniGrok MCP Gateway",
      "description": "Shared Grok agent with live Control Center, cost tracking, reasoning guard, OKF + WebMCP self-discovery",
      "headers": { "X-Client-ID": "cursor" }
    }
  }
}
```

Cursor auto-detects HTTP servers from the `url` field. After saving, enable
the server under Settings → MCP; the `agent` tool appears in Composer/chat.

## Claude Code (CLI)

```bash
claude mcp add --transport http unigrok http://localhost:4765/mcp \
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
        "-y", "mcp-remote", "http://localhost:4765/mcp",
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
      "url": "http://localhost:4765/mcp",
      "headers": { "X-Client-ID": "vscode" }
    }
  }
}
```

## Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.grok]
url = "http://localhost:4765/mcp"
http_headers = { "X-Client-ID" = "codex" }
```

(Field names vary slightly across Codex releases; if `url` isn't accepted,
your version may want the experimental streamable-HTTP client enabled —
check `codex mcp --help`. Keep the server name as `grok`; this repo's
`.codex/mcp/grok-routing.json` and Codex intelligence config route to the
`mcp__grok` tool namespace.)

## Antigravity / Gemini (`settings.json` → MCP servers)

```json
{
  "mcpServers": {
    "unigrok": {
      "httpUrl": "http://localhost:4765/mcp",
      "headers": { "X-Client-ID": "antigravity" }
    },
    "unigrok-forge": {
      "httpUrl": "http://localhost:4766/mcp",
      "headers": { "X-Client-ID": "antigravity-forge" }
    }
  }
}
```

> [!NOTE]
> IDEs cache MCP schemas. After adding or changing a tool, reconnect the Forge
> MCP entry or reload the IDE window (in Antigravity: **Developer: Reload
> Window**) before deciding the tool is missing.

## Exercise Code Swarm safely

Start with `UNIGROK_SWARM=dry_run`. It searches and scores candidates but
refuses every apply request. The golden repository target is:

```text
start_code_swarm(
  target_path="evals/tasks/swarm_targets/nsquared_dedup/dedup.py",
  focus_node="function:dedup",
  test_target="evals/tasks/swarm_targets/nsquared_dedup/test_dedup.py",
  bench_command="python evals/tasks/swarm_targets/nsquared_dedup/bench_dedup.py"
)
```

Poll the returned id with `get_swarm_status(task_id)` or request the complete
replay contract with `get_swarm_status(task_id, view="json")`, then load the id
in the Pareto Playground. The four outcomes are `static_wall`, `test_wall`,
`dominated`, and `pareto_elite`; values without benchmark measurements stay in
the wall gutter rather than receiving invented coordinates.

`UNIGROK_SWARM=active` enables `apply_swarm_winner` only for a completed or
cancelled run's current verified Pareto front and only while the target hash is
current. Apply re-runs `test_target`, restores the file on failure, and never
commits. Here “verified” means exactly that the supplied tests passed. Review a
JSON export before publishing it: Pareto elites intentionally include their
replacement source and a current live payload can include the original span.

## What the IDEs get

The public MCP surface centers on the `agent` tool (modes:
auto/fast/reasoning/thinking/research) — UniGrok routes across Grok models,
runs xAI server-side tools, and remembers per-client sessions. The stable
service cannot browse the IDE's open folder. IDE agents may attach selected
text using `workspace_context` and an optional `workspace_label`. `/metrics` (JSON or
`?format=prometheus`) shows per-caller usage.

`agent` returns `response` plus execution metadata: `route`, `plane`, `model`,
`why` (`pin`, `cost`, `auto`, or `failover`), `degraded`, `profile`,
`finish_reason`, token/cost totals, latency, and citations when upstream
provides them. `degraded=true` means the run fell back from the initially
selected route or plane.

The local CLI plane works **inside the container**: the image bakes the Linux
`grok` binary (version-pinned in the Dockerfile), while compose persists its
machine-level OAuth session in the dedicated `unigrok-cli-auth` Docker volume.
Authenticate that service identity once with:

```bash
docker compose run --rm grok-cli-auth
```

The device-code flow is deliberately separate from ordinary startup and from
every IDE project. UniGrok strips `XAI_API_KEY` from CLI subprocesses, so a CLI
route must use verified grok.com OAuth instead of silently consuming API quota.
The startup log and `/runtimez` report `ready`, `needs_auth`, `unreachable`, or
other bounded state. When CLI auth is absent, the container still starts and
the API plane remains available.

After the service is running, use the project-independent `setup_command`
returned by `/runtimez` or `grok_mcp_status`. It repairs ownership of older
named volumes, drops to the unprivileged service uid, removes API-key variables,
and starts `grok login --device-auth`. The command is intentionally returned by
the service so agents do not guess a stale bootstrap sequence.
