<div align="center">

<img src="assets/hero.svg" alt="UniGrok · Grok MCP Server & Gateway — one Grok server, every IDE, zero pasted API keys" width="100%"/>

[![CI](https://img.shields.io/github/actions/workflow/status/djtelicloud/grok-mcp-server/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/djtelicloud/grok-mcp-server/actions)
[![Release](https://img.shields.io/github/v/release/djtelicloud/grok-mcp-server?style=flat-square&label=release)](https://github.com/djtelicloud/grok-mcp-server/releases)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?style=flat-square)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-black?style=flat-square)](https://modelcontextprotocol.io)
[![xAI Grok](https://img.shields.io/badge/xAI-Grok-000000?style=flat-square)](https://docs.x.ai/?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=badge-docs)

[Quick Start](#quick-start) · [IDE Setup](#ide-setup) · [Public Project Site](#public-project-site-and-contributor-control) · [Tool Surface](#tool-surface) · [Architecture](#architecture) · [Security](#security-model)

</div>

# UniGrok · Grok MCP Server & Gateway

> **What is UniGrok?** One local Grok server that every coding agent on your
> machine shares — self-routing across xAI's API and the Grok CLI
> subscription, with per-call cost tracking, while your API key never leaves
> the server.

UniGrok is a local-first **Grok MCP server and gateway** for
[xAI's Grok models](https://docs.x.ai/?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=intro-docs).
It runs once on your machine, keeps the xAI credential on the server side, and
lets Cursor, Claude Desktop, Claude Code, VS Code, Codex, Antigravity, and
other MCP clients share the same Grok agent over Streamable HTTP — with
dual-plane routing across the xAI API and the Grok CLI subscription, per-call
cost tracking, and a browser Control Center.

![UniGrok architecture — six MCP clients share one local gateway that routes across the metered xAI API plane and the ~$0-marginal Grok CLI plane, with SQLite-backed sessions, cost, and jobs](assets/architecture.svg)

Current release: **v0.6.0**.

Use it as:

- A shared multi-IDE Grok MCP server at `http://localhost:4765/mcp` (`4765` spells **GROK** on a phone keypad).
- An OpenAI-compatible local gateway for `unigrok-agent`.
- A structured agent harness with web search, X search, code execution, files,
  image/video generation, session memory, telemetry, and reflection.

## Quick Start

This setup is designed to be copy-pasteable. You need Git, Docker Desktop, and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/). You do **not**
need to understand MCP internals.

```bash
git clone https://github.com/djtelicloud/grok-mcp-server.git
cd grok-mcp-server
uv run python main.py init
```

The init command:

- copies `example.env` to `.env` when `.env` does not already exist;
- leaves an existing `.env` untouched;
- prints copy-paste configs for VS Code, Claude Desktop, Claude Code, and Codex;
- points every IDE at the shared HTTP endpoint instead of asking each IDE for
  the raw xAI key.

Choose at least one credential path:

- **SuperGrok subscription:** use the CLI device login below. This is the
  preferred path for compatible requests and does not require an API key.
- **xAI developer API:** edit `.env` and replace the placeholder with a key
  from the [xAI Console](https://console.x.ai/?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=quickstart-get-key).
- **Both:** recommended for the broadest model and tool coverage. UniGrok
  keeps the two credentials and their usage accounting separate.

For the developer API path:

```bash
XAI_API_KEY=your_real_xai_api_key
```

Start the shared service:

```bash
docker compose up --build -d
curl -s http://localhost:4765/healthz
```

For the SuperGrok subscription path, authenticate once per machine:

```bash
docker compose run --rm grok-cli-auth
```

The helper uses xAI's device-code login and stores the refreshable OAuth state
in the dedicated `unigrok-cli-auth` Docker volume. It is service identity, not
project identity: never repeat this when switching repositories. Ordinary
startup is noninteractive. The service is usable when either the API plane or
the CLI plane is ready; features that exist only on the other plane remain
unavailable until that credential is configured.

You are done when health reports `{"status":"healthy"}` and the Control Center
at `http://localhost:4765/ui/` says the gateway is live. Use host port `4765`
for IDEs and browsers; `8080` is only the container's internal port.

For an explicit no-API-billing agent call, set `plane="cli"` and
`fallback_policy="same_plane"`. Use `plane="api"` for a strict metered API
call. The default `plane="auto"` remains backward compatible; the Control
Center defaults to the safer subscription-only contract.

This is a standalone, workspace-neutral service. The image runs its baked
application from `/app`, keeps mutable data in a Docker volume at `/state`, and
does **not** mount this repository or whichever project an IDE currently has
open. Register the endpoint globally once, then switch projects freely; those
projects need no `.agents`, `.codex`, `.grok`, or other UniGrok files.

When Grok needs project material, the calling IDE should send deliberately
selected excerpts, diffs, errors, or other context in `agent.workspace_context`.
UniGrok never guesses that MCP registration grants filesystem access.

Open the local Control Center:

```text
http://localhost:4765/ui/
```

## Public Project Site and Contributor Control

The source for the canonical public UniGrok Site lives in
[sites/unigrok-control-center](sites/unigrok-control-center/README.md) and is
bound to the existing project by its checked-in, non-secret Site project ID. It
is not an idless installer template. The public root and machine-readable
project routes require no account; `/control` is a separate protected surface.

The public Site redirects `/control` to `https://control.grokmcp.org`, where
GitHub App OAuth establishes identity and a fresh installation-token lookup
checks repository permission on every protected request. A fail-closed
`UNIGROK_GITHUB_IDENTITY_BINDINGS` adapter remains only as the Sites rollback
fallback; it is not the canonical authorization path.

The hosted Site does not accept provider credentials and does not pretend it
can reach a contributor's laptop through `localhost`. Local development and
the outbound Secure MCP Tunnel boundary stay separate. Codex/project-admin
automation owns the Site binding and deployment review; every Sites deployment
URL is production and must pass the deployment-source gate before publication.

## Install Script

For a guided local bootstrap:

```bash
./install.sh
```

It checks for `uv`, `git`, and Docker, syncs the Python environment, runs
`init`, and validates Docker Compose when Docker is available.

The stable gateway's base package excludes test/lint tooling. Contributor
Forge/Swarm installations need `uv sync --extra forge`; the repository's
default development group and Docker image already include that extra's tools.

## IDE Setup

The default architecture is one shared Docker service:

```text
http://localhost:4765/mcp
```

Each IDE should send a stable `X-Client-ID` header for attribution and to keep
that authenticated principal's IDE sessions separate. The header is an
untrusted label, not an authentication credential; remote budgets and session
isolation bind to the OAuth subject or configured gateway-key alias.

### Cursor

With Cursor joining the xAI family (SpaceX's June 2026 agreement to acquire
Anysphere), it's the natural first-class Grok IDE — add UniGrok in 10 seconds.
Create or edit `.cursor/mcp.json` in your project root (or `~/.cursor/mcp.json`
globally) and paste:

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

### VS Code

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

### Claude Desktop

Claude Desktop config-file servers are stdio commands, so bridge to HTTP with
`mcp-remote`:

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

### Claude Code

```bash
claude mcp add --transport http unigrok http://localhost:4765/mcp \
  --header "X-Client-ID: claude-code"
```

### Codex

```toml
[mcp_servers.grok]
url = "http://localhost:4765/mcp"
http_headers = { "X-Client-ID" = "codex" }
```

If `UNIGROK_API_KEYS` is set in `.env`, also add
`Authorization: Bearer <token>` to each client config.

More detail, including Antigravity/Gemini notes, lives in
[docs/ide-setup.md](docs/ide-setup.md).

The deployment and identity assumptions are explicit in the
[threat model](docs/threat-model.md).

### ChatGPT and GitHub `@grok` reviews

The public MCP also exposes a read-only `review_pull_request` tool with a
ChatGPT Apps widget. An optional self-hosted GitHub workflow can fetch PR
evidence through GitHub's API, ask the local subscription plane for a review,
and maintain one advisory PR comment for Codex. The comment names the exact
reviewed head/base commits and a digest of the bounded evidence, and the
workflow refuses to publish if the PR head changes during review. It never
executes contributor code or grants Grok merge authority. See
[docs/chatgpt-github-app.md](docs/chatgpt-github-app.md) for the private
ChatGPT App, Secure MCP Tunnel, runner, permissions, and threat model.

## Run Modes

Stdio MCP:

```bash
uv run python main.py
```

HTTP gateway:

```bash
uv run python main.py --http
```

Packaged console script:

```bash
uv run unigrok-mcp init
uv run unigrok-mcp --http
```

Supervised helper:

```bash
./grok-mcp-helper.sh init
./grok-mcp-helper.sh start
./grok-mcp-helper.sh status
```

## Tool Surface

Start with `agent`. It is the headline tool and should handle most nontrivial
requests.

The stable IDE-facing HTTP service intentionally starts with a small public
surface centered on:

- `agent`: auto-routed Grok agent with modes `auto`, `fast`, `reasoning`,
  `thinking`, and `research`.
- status and discovery tools that explain readiness without running inference.

Trusted stdio and contributor modes additionally expose specialist tools such
as:

- `grok_reflect`: focused structured critique for plans, code-review notes,
  outputs, and architecture decisions.
- `chat`: plain Grok chat with optional model pinning and session history.
- `chat_with_vision`: image analysis.
- `chat_with_files`: grounded answers over uploaded xAI files.
- `submit_research_job`, `get_research_job`, `list_research_jobs`: deferred
  xAI research jobs.
- `remember_fact`, `search_knowledge`, `forget_fact`, `distill_session`: local
  knowledge memory.
- `recall_workspace_memory`, `record_landed_outcome`,
  `explain_workspace_evidence`, `workspace_memory_status`: explicit,
  contributor-only, commit-anchored engineering evidence for agents developing
  UniGrok itself. Records require a verified `scripts/land` receipt; automatic
  prompt injection is off. These tools are not on the public HTTP service.
- `start_code_swarm`, `get_swarm_status`, `apply_swarm_winner`,
  `cancel_swarm`: contributor-only, single-span Python optimization with the
  caller's tests as the correctness boundary. Generation is CLI-only;
  `dry_run` cannot apply, while `active` applies only a terminal run's current
  verified Pareto front and never commits. `/ui/swarm.html` renders the same
  measured-only JSON status contract used by static exports.
- `web_search`, `x_search`, `remote_code_execution`: xAI server-side tools.
- `read_local_file`, `list_project_files`, Git inspection, tests, and guarded
  writes: local contributor capabilities that are not implied by registering
  the stable workspace-neutral service.
- `generate_image`, `generate_video`, `extend_video`: Grok Imagine media.

### Explainable model selection

`agent(model=None, mode="auto")` uses one deterministic, local-first selector:

- capability classes are `planning`, `coding`, `vision`, and `research`;
- planning cold-starts on `grok-4.5`, coding on `grok-build-0.1`, and research
  on the live Grok 4.20 multi-agent slug;
- explicit model pins and `UNIGROK_*_MODEL` overrides win only after strict
  plane/catalog compatibility validation;
- the live catalog is cached for 15 minutes and a discovery failure uses the
  bundled model directory instead of blocking a request;
- fresh eval calibration is considered before local telemetry, but a peer
  needs mature evidence and a 15-point success-rate advantage to replace the
  stable default.

Every `AgentResult` includes a `routing` receipt, and new telemetry rows retain
that same prompt-free receipt. It explains the task feature bucket, route
class, candidate models, evidence source, selected model, pin source, and any
failover. The Control Center renders these receipts directly rather than
guessing a reason from aggregate metrics.

The public Streamable HTTP MCP endpoint intentionally exposes the unified
`agent` surface for IDE use. The full stdio server exposes the broader tool
set for local trusted workflows.

Workspace-memory operations are also available locally as
`unigrok-mcp memory status`, `unigrok-mcp memory sync`, and
`unigrok-mcp memory import`. The Git Notes ref is local provenance and is not
part of ordinary branch pushes.

The public HTTP surface stays intentionally small: `agent`, status, discovery,
and the disabled-by-default maintenance helper. In unrelated projects, call
`agent` normally and add `workspace_context` only when local project evidence
is needed.

## Architecture

UniGrok has three boundaries:

- Transport: stdio MCP, Streamable HTTP MCP, and an OpenAI-compatible `/v1`
  facade all route into the same agent harness.
- Model plane: authenticated local Grok CLI is preferred for compatible,
  unpinned work; API-backed Grok models serve explicit pins and API-native
  thinking, vision, and multi-agent research capabilities.
- Local state: SQLite stores sessions, telemetry, research jobs, task memory,
  distilled knowledge, and commit-anchored workspace evidence under the
  configured state directory.

```mermaid
flowchart LR
    CU[Cursor] --> GW
    CC[Claude Code] --> GW
    VS[VS Code] --> GW
    CX[Codex] --> GW
    AG[Antigravity] --> GW
    GW["UniGrok gateway<br/>localhost:4765 (GROK)<br/>/mcp · /v1 · /ui"]
    GW -->|API plane · XAI_API_KEY| API["xAI API<br/>grok-4.5 · grok-build-0.1"]
    GW -->|CLI plane · OAuth subscription| CLI["Grok CLI live catalog<br/>grok-4.5 · composer"]
    GW --- ST[("SQLite<br/>sessions · cost · jobs")]
```

Full design detail lives in [architecture.md](architecture.md).

UniGrok strips `XAI_API_KEY` from every CLI subprocess. This prevents a CLI
invocation from silently charging the API credential and makes the reported
CLI/API routing split a real credential and allowance boundary.

The Control Center usage ledger keeps those planes honest: xAI API requests
store the exact per-response billed cost; CLI subscription requests store local
counts, latency, success, model, and estimated tokens without inventing a
per-request dollar cost. xAI does not expose SuperGrok subscription quota, so
UniGrok never invents remaining allowance or merges API billing into CLI
statistics. An optional advanced organization-billing comparison exists, but
ordinary users do not need a team id or management key for local telemetry.

### Credential-plane onboarding

`grok_mcp_discover_self`, `grok_mcp_status`, `/runtimez`, and every public
`agent` result expose the same non-secret `credential_planes` contract. A fresh
IDE agent should inspect its notices once per state:

- If CLI is missing or unauthenticated, continue on API when possible and ask
  permission before rebuilding/installing the CLI or starting device auth.
- If `XAI_API_KEY` is missing but CLI is ready, prompt once without blocking
  compatible CLI work; an API-only capability blocks until the key is securely
  configured.
- If both planes are unavailable, stop model work and present both repair
  actions. Never request the API key in chat or write it into the caller's
  project; it belongs only in the global UniGrok service environment.

The local default is `UNIGROK_PLANE_POLICY=cli_first`. Explicit model pins and
`UNIGROK_*_MODEL` overrides still win. Set the policy to `api_first` only when
API-native behavior is intentionally preferred over subscription utilization.
CLI-first model slugs come from the authenticated live `grok models` catalog,
not a hard-coded subscription catalog; coding currently prefers composer while
reasoning uses the live CLI default.

Useful endpoints in HTTP mode:

- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `GET /metrics?format=prometheus`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /mcp`

## WebMCP & OKF Discovery

UniGrok combines Google's **Open Knowledge Format (OKF)** with the experimental
**WebMCP** API currently published as a W3C Web Machine Learning Community
Group draft. WebMCP is not yet a W3C Standard.

### 1. OKF Knowledge Bundle
The directory `/docs/okf/` contains a fully self-describing documentation bundle for agents:
- `okf-manifest.json`: Lists index and topic documents.
- `index.md`: Main entrypoint for agent reading.
- Topic-specific files (e.g. `agent-tool.md`, `reasoning-guard.md`) detail tool schemas, inputs/outputs, model pinning, and telemetry budget controls.

### 2. WebMCP-Enabled Docs & Console
When running the HTTP gateway, visiting `http://localhost:4765/ui/` exposes browser-native WebMCP tools under `document.modelContext`.
Any agent visiting this page can automatically discover and call:
- `get_schema(tool_name)`: Returns the Pydantic JSON schema of a given UniGrok tool.
- `example_call(mode)`: Returns JSON templates/examples for different operational modes.
- `simulate_reasoning_guard`: Simulates checking if a model meets the required reasoning level.
- `fetch_okf_bundle`: Returns the metadata and file paths in the OKF bundle.

### 3. Pre-Visit Manifest Discovery
A project-specific experimental manifest is exposed at `/.well-known/webmcp`
so compatible agents and extensions can pre-discover the page's capabilities
without performing heavy DOM scrapes:
```bash
curl -s http://localhost:4765/.well-known/webmcp
```

### 4. Running a WebMCP-Compatible Browser or Bridge
To let an IDE agent call these experimental browser tools, use a browser build
or extension that exposes `document.modelContext`, and keep the target tab at
`http://localhost:4765/ui/` open.

## Troubleshooting / FAQ

<details>
<summary><strong>Port 4765 is already in use</strong></summary>

UniGrok publishes its local service on host port `4765`. Stop the conflicting
process or set another host port in `.env`, then recreate the service:

```dotenv
UNIGROK_PORT=9090
```

```bash
docker compose up --build -d
curl http://localhost:9090/healthz
```

The Control Center is then at `http://localhost:9090/ui/` and MCP at
`http://localhost:9090/mcp`. Port `8080` is container-internal and does not
belong in IDE configuration.

</details>

<details>
<summary><strong>Docker Compose fails to start</strong></summary>

Make sure Docker Desktop (or the Docker daemon on Linux) is running, then:

```bash
docker compose down
docker compose up --build -d
docker compose ps
```

On Windows with WSL2, ensure WSL integration is enabled. If startup still
fails, inspect `docker compose logs grok-mcp`.

</details>

<details>
<summary><strong>Authentication or model access fails</strong></summary>

Verify the API key held by the UniGrok service:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${XAI_API_KEY}" \
  https://api.x.ai/v1/models
```

For the subscription-backed CLI plane, open **Setup & Status** in the Control
Center. If authentication is missing, run `docker compose run --rm
grok-cli-auth` and complete the device-code login. UniGrok uses `grok --check`
inside the service as its readiness probe.

</details>

<details>
<summary><strong><code>mcp-remote</code> cannot connect</strong></summary>

1. Confirm the server is running: `curl http://localhost:4765/healthz`.
2. Point the IDE at `http://localhost:4765/mcp`, not `/sse`.
3. Restart the IDE's MCP client after changing its configuration.

</details>

<details>
<summary><strong>Requests hang or time out</strong></summary>

- Inspect `docker compose logs -f grok-mcp`.
- Check connectivity to `api.x.ai`.
- Increase Docker memory if the container was OOM-killed.
- Inspect the route and degradation metadata in the Control Center.

</details>

## Security Model

- `XAI_API_KEY` belongs in the UniGrok server/container environment, not in
  each IDE client.
- `example.env` is a template only. The runtime loads `.env` when present and
  rejects the placeholder key.
- Docker publishes `127.0.0.1:4765` by default.
- Set `UNIGROK_API_KEYS` before exposing the gateway beyond loopback.
- Git write tools are disabled unless local runtime flags explicitly enable
  them.
- Container restart is disabled by default and should only be enabled for a
  trusted local process with Docker access.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment
guidance.

## Development

```bash
uv sync
uv run pytest
uv run python -m compileall -q src evals main.py
docker compose config
docker compose -f docker-compose.dev.yml config
```

Contributors who want live mounted source use the separate service on port
4766:

```bash
# Edit .env first: UNIGROK_SWARM=dry_run
docker compose -f docker-compose.dev.yml up --build -d
curl -s http://localhost:4766/healthz
```

That contributor endpoint conditionally adds the commit-anchored memory tools,
Code Swarm tools, and repository-mounted test/file facilities. The dev Compose
file supplies contributor mode and `WORKSPACE_ROOT=/workspace`; model
credentials stay in `.env`, never in IDE JSON. See
[IDE setup](docs/ide-setup.md#exercise-code-swarm-safely) for the golden dry-run
call, rollout ladder, and export warning. These tools never appear on the
stable service used by unrelated projects.
Open `http://localhost:4766/ui/swarm.html` to inspect a returned task id.

`scripts/land` may reconcile that contributor service after tests pass. It
never rebuilds or restarts the stable port-4765 service automatically.

### Optional Grok Dial Plan

UniGrok can expose memorable phoneword “speed-dial” ports without creating
extra services or databases. Enable the overlay with:

```bash
docker compose -f docker-compose.yml -f docker-compose.dials.yml up --build -d
```

| Dial | Phoneword | Default `agent` mode |
|---:|---|---|
| `2886` | AUTO | `auto` |
| `3278` | FAST | `fast` |
| `7327` | REAS | `reasoning` |
| `8465` | THNK | `thinking` |
| `7724` | RSCH | `research` |

Every dial reaches the same stable process, sessions, authentication, and
Control Center. The original `Host` port supplies a default only when the MCP
caller omits `mode`; an explicit tool argument always wins. Normal users should
register `4765` once. The dial overlay is an optional power-user interface.

Run the full local test suite before publishing changes. Offline evals can be
run with:

```bash
uv run python -m evals run --check-baseline
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution workflow and PR
expectations. The staged public site, protected contributor control plane,
GitHub authorization, private OAuth MCP, hosted review, and landing governance are defined in
[ADR 0001](docs/adr/0001-cloud-control-plane-governance.md). The ADR clearly
separates the live read-only and receipt-verification services from the
deliberately disabled cloud merge/release mutation boundary.
The production resource boundary and rollback procedure are in
[Private remote MCP deployment](docs/remote-mcp-deployment.md).

---

Built by [@DavidLJohnston](https://x.com/DavidLJohnston?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=footer-author)
· Built for [Grok](https://grok.com/?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=footer-grok)
· Powered by [xAI](https://x.ai/?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=footer-brand)
· Follow [@xai](https://x.com/xai?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=footer-x)
on X
