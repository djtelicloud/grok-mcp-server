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
cd /path/to/grok-mcp-server     # the primary checkout, not a task worktree
uv run python main.py init      # first time only; creates .env if absent
docker compose up -d --build
curl --fail -s http://localhost:4765/healthz
```

Choose at least one credential path:

- **SuperGrok subscription:** run `docker compose run --rm grok-cli-auth` and
  complete the device-code login. No `XAI_API_KEY` is required for compatible
  CLI-plane work.
- **xAI developer API:** edit `.env` and replace the `XAI_API_KEY` placeholder.
- **Both:** recommended for maximum coverage. The default `cli_first` policy
  prefers compatible subscription work while preserving API-only features.

After configuring at least one credential path, run:

```bash
curl --fail -s http://localhost:4765/readyz
```

`/healthz` only proves that the HTTP process is alive. A successful `/readyz`
checks API credential presence or a live CLI OAuth probe, writable state, and
SQLite. It does not spend a request to validate an API key. Open
`http://localhost:4765/ui/` and use **Setup & Status** to confirm which
credential planes are ready before configuring every IDE.

Compose loads secrets from the launched checkout's `.env`. Agent worktrees
often do not have that gitignored file; set
`UNIGROK_ENV_FILE=/path/to/grok-mcp-server/.env` before `docker compose up` to
use the primary checkout's secret file.

The stable compose file runs the image's baked application at `/app`, stores
mutable data in a Docker volume mounted at `/state`, and publishes
`127.0.0.1:4765`. It does not mount the UniGrok checkout or an IDE project.
Compose declares that this is a trusted loopback-only host publication so the
local service can run without a client token. Any direct non-loopback bind must
enable authentication: static deployments use `UNIGROK_API_KEYS`; the private
Cloud Run deployment instead uses `UNIGROK_OAUTH_INTROSPECTION_URL` and the
associated OAuth discovery settings. Remove the
`UNIGROK_TRUSTED_LOOPBACK_PROXY` declaration and configure one of those
documented authentication boundaries before changing the port mapping to
`0.0.0.0:4765:8080` or `4765:8080`.

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

Every config below sends `X-Client-ID`. It attributes telemetry and separates
one authenticated principal's IDE sessions. It is a caller-controlled label,
not a security principal: the stored namespace is derived from the OAuth
subject or gateway-key alias first, then the client label. For example, two
IDEs under the same local principal keep distinct `vscode:main` and
`claude-desktop:main` logical sessions, while two OAuth subjects cannot collide
even if both assert `X-Client-ID: vscode`. Budgets bind to the authenticated
principal. Omitting the header shares that principal's client-neutral
namespace.

If the local/static gateway sets `UNIGROK_API_KEYS`, also add
`"Authorization": "Bearer <one-of-those-keys>"` to each config's headers.
OAuth-protected remote clients obtain a scoped access token through the
published RFC 9728 metadata; they do not reuse an xAI key or a local static
gateway token.

## Cursor (first-class host IDE)

**Cursor is UniGrok’s preferred host IDE** (xAI family). SuperGrok CLI remains a
**credential plane** (subscription auth for Grok models), not the long-term IDE
product surface. Use Cursor (or any MCP client) as the place you type; use
UniGrok as the shared Grok gateway with dual-plane cost truth.

Put config in `~/.cursor/mcp.json` (user-global) or project `.cursor/mcp.json`
(not the repo-root `.mcp.json`, which is the VS Code / Copilot path and uses
`vscode` / `vscode-forge` labels):

```json
{
  "mcpServers": {
    "unigrok": {
      "url": "http://localhost:4765/mcp",
      "name": "UniGrok MCP Gateway",
      "description": "Shared Grok agent with live Control Center, cost tracking, OKF + WebMCP self-discovery",
      "headers": { "X-Client-ID": "cursor" }
    },
    "unigrok-forge": {
      "url": "http://localhost:4766/mcp",
      "headers": { "X-Client-ID": "cursor-forge" }
    }
  }
}
```

Cursor auto-detects HTTP servers from the `url` field. After saving, enable
the server under Settings → MCP; the `agent` tool appears in Composer/chat.
Confirm Control Center / telemetry shows caller label `cursor` or
`cursor-forge` (for example `http:anon|cursor`) — never bare `http:anon` and
never `vscode` from a Cursor session. Bare `http:anon` means the
`X-Client-ID` header was omitted. Repo-root `.mcp.json` staying on
`vscode` / `vscode-forge` is intentional for VS Code; Cursor must keep its
own `cursor` / `cursor-forge` labels in `~/.cursor/mcp.json` or
`.cursor/mcp.json`. Repo rule
[`.cursor/rules/cursor-automations-single-pass.mdc`](../.cursor/rules/cursor-automations-single-pass.mdc)
encodes PR Approver / Security Reviewer / Bugbot Autofix single-pass discipline.

### Cursor attribution smoke (live check)

After connect, prove the label from inside Cursor (not from docs alone):

1. Call `grok_mcp_discover_self` and confirm `data.request_context.client_id_present`
   is true and `client_id_normalized` is `cursor` (or `cursor-forge` on the
   Forge entry).
2. Note `grok_mcp_status` Top Callers baseline, then run one cheap
   `agent` call with `mode=fast` and a unique session marker.
3. Re-check Top Callers: `http:anon|cursor` (or `|cursor-forge`) should
   increase by one. Bare `http:anon` means some other client omitted
   `X-Client-ID` — it is not the healthy Cursor path above.

### Bugbot Autofix live fidelity smoke

After the Autofix fidelity contract is Live, verify Automations still obey it
before trusting the next Autofix run:

1. Confirm shared law and Cursor mirror still match: `.agents/AGENTS.md` →
   **Cursor Automations** and
   [`.cursor/rules/cursor-automations-single-pass.mdc`](../.cursor/rules/cursor-automations-single-pass.mdc)
   both include **Autofix fidelity** (one cited finding → one minimal fix →
   one push) plus the Composer role gate (interactive chat is not Autofix
   authorization).
2. On the next real Bugbot finding: Autofix must stay on the given PR head,
   touch only the cited finding, push once, and exit if another
   Autofix / Approver / Security run is already active for that head.
3. Fail the smoke if Autofix spawns peers, reopens “review modules,” or
   “also fixes” adjacent nits — that is thrash, not fidelity.

Interactive Composer (including Cursor Grok 4.5 chat) remains outside these
automation paths; use UniGrok `@grok` peer review when you want dual-plane
cost/route honesty instead of an Autofix mutation.

### Cursor attribution smoke (live check)

After connect, prove the label from inside Cursor (not from docs alone):

1. Call `grok_mcp_discover_self` and confirm `data.request_context.client_id_present`
   is true and `client_id_normalized` is `cursor` (or `cursor-forge` on the
   Forge entry).
2. Note `grok_mcp_status` Top Callers baseline, then run one cheap
   `agent` call with `mode=fast` and a unique session marker.
3. Re-check Top Callers: `http:anon|cursor` (or `|cursor-forge`) should
   increase by one. Bare `http:anon` means some other client omitted
   `X-Client-ID` — it is not the healthy Cursor path above.

Repo-root `.mcp.json` may still use `vscode` / `vscode-forge` for the VS Code
path; Cursor must keep `cursor` / `cursor-forge` in `~/.cursor/mcp.json` or
project `.cursor/mcp.json` so the two IDEs do not thrash labels.

### Cursor multi-model vs UniGrok planes

Cursor may list **native** models for Composer/chat (including **Grok 4.5**,
Claude, GPT, etc.). Those paths are **Cursor-native billing and routing** —
they are not UniGrok credential planes and will not appear on Control Center →
**Planes**.

| Path | Who bills / routes | Where you see models | Use when |
|------|--------------------|----------------------|----------|
| Cursor-native Grok 4.5 (Composer) | Cursor / xAI via Cursor | Cursor model picker | Ordinary IDE-local edits and Automations loops |
| Cursor native non-Grok | Cursor / that provider | Cursor model picker | You intentionally want a non-Grok host model |
| UniGrok MCP `agent` (default route) | UniGrok CLI sub and/or xAI API key | Control Center **Planes** + MCP | Shared Grok, `@grok` peer review, dual-plane cost/route receipts |
| UniGrok MCP `agent` pinned `grok-build-0.1` | UniGrok API plane (model pin; no new plane) | Control Center **Planes** + MCP | Code-heavy implementation via UniGrok |

**Decision card (Cursor Grok 4.5 session):**

- Stay on **Cursor-native Grok 4.5** for ordinary Composer coding and
  Automations work inside this IDE.
- Call **UniGrok** (`agent` / `@grok`) when you need cross-IDE continuity,
  CLI vs API plane truth, metered cost receipts, or a second opinion that
  other brands can also query through `http://localhost:4765/mcp`.
- Pin **`grok-build-0.1`** on UniGrok `agent` for code-heavy implementation —
  it selects an API-plane model, not a separate credential plane.
- Do not treat Cursor-native Grok 4.5 as a UniGrok plane — Control Center will
  not show that spend under **Planes**.

#### Live routing receipt (Cursor Build exercise)

Same micro-prompt run once pinned to Build and once on UniGrok `fast`
(2026-07-15, caller `cursor`):

| Lane | Model | Plane | Cost | Note |
|------|-------|-------|------|------|
| UniGrok Build pin | `grok-build-0.1` | API | metered (~$0.004) | Short bullets; weaker plane disclaimer |
| UniGrok fast | `grok-composer-2.5-fast` | CLI | subscription $0 | Clearer “not a separate plane” bullet |
| Cursor Composer | Grok 4.5 | Cursor-native | Cursor billing | Applied one Autofix-style docs fix from a cited gap |

This section consolidates the Cursor Ready fragment packets (attribution smoke,
native vs UniGrok routing, Build pin + receipt). Codex may close or squash
those drafts when this lands; leave Claude / Copilot / Gemini packets alone.

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

## Antigravity / Gemini (`.gemini/settings.json`)

Antigravity configures its MCP servers in the tracked project file `.gemini/settings.json`.
The configuration natively sets `allowNonWorkspaceAccess: false` and restricts tool access to `mcp(grok/*)` to ensure the agent cannot mutate the host outside of the worktree.

> [!WARNING]
> **Tracked Configs Only**: Never replace the repository's `.gemini/config.json` or `.gemini/settings.json` with a private host credential file (like your `~/.gemini/config`). These files must remain public project configuration.
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

> [!WARNING]
> **Secret & Worktree Safety**
> - **No Secrets in Configs**: Never copy `XAI_API_KEY`, Google ADC credentials, or host `~/.gemini/config` files into this repository or the IDE MCP config JSON. 
> - **Isolated Worktrees**: Operate inside `.worktrees/gemini/<task>/` (or the provider home). Do not pollute Documents with loose checkouts or mutate the primary shared `main` checkout.


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

**Session continuity note:** UniGrok maps each logical MCP `session` name to a
native CLI conversation id. The CLI binds an agent type to that native id, so
composer/`fast` then planning/`reasoning` on the same logical session may hit
`MODEL_SWITCH_INCOMPATIBLE_AGENT`. Current builds recover by opening a fresh
native session and replaying server-side history (fork would keep the sticky
agent). If you still see the raw CLI error after a mode switch, rebuild the
stable image from current product `main` so recovery code is loaded.

Authenticate that service identity once with:

```bash
docker compose run --rm grok-cli-auth
```

The device-code flow is deliberately separate from ordinary startup and from
every IDE project. UniGrok strips all server-owned provider, management,
gateway, and credential-file variables from CLI subprocesses, so a CLI route
must use verified grok.com OAuth instead of consuming API quota or exposing a
subordinate-provider credential.
The startup log and `/runtimez` report `ready`, `needs_auth`, `unreachable`, or
other bounded state. When CLI auth is absent, the container still starts and
the API plane remains available.

After the service is running, use the project-independent `setup_command`
returned by `/runtimez` or `grok_mcp_status`. It repairs ownership of older
named volumes, drops to the unprivileged service uid, removes server credentials,
and starts `grok login --device-auth`. The command is intentionally returned by
the service so agents do not guess a stale bootstrap sequence.
