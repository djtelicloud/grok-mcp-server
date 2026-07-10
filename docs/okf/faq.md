---
okf_version: "0.1"
faq_schema_version: "1"
source_version: "0.5.3"
title: "UniGrok FAQ"
type: "topic"
description: "Verified local-first setup, routing, security, and troubleshooting answers for UniGrok MCP users."
---

# UniGrok FAQ

This document is the canonical, release-versioned source for operational
UniGrok answers. Each entry has a stable anchor ID and curated keywords so Grok
can retrieve a verified answer without inferring operational commands.

## How do I connect Cursor to UniGrok? {#cursor-connect}

**Keywords:** cursor, cursor mcp, mcp.json, connection, localhost

Start the shared gateway, then create or edit `.cursor/mcp.json` in your
project root (or `~/.cursor/mcp.json` globally):

```json
{
  "mcpServers": {
    "unigrok": {
      "url": "http://localhost:4765/mcp",
      "name": "UniGrok MCP Gateway",
      "headers": { "X-Client-ID": "cursor" }
    }
  }
}
```

Enable the server under Cursor **Settings → MCP**. Cursor and the gateway must
run on the same machine: `localhost` inside a remote development container or
SSH host refers to that remote environment, not your laptop.

## Do I need an xAI API key in every IDE? {#shared-api-key}

**Keywords:** api key, xai api key, credentials, ide, shared gateway

No. Set `XAI_API_KEY` only in UniGrok's server/container `.env` file. Every IDE
connects to the local MCP endpoint at `http://localhost:4765/mcp`; do not paste
the upstream xAI key into Cursor, Claude Code, VS Code, Codex, or Claude
Desktop configurations.

If you configure `UNIGROK_API_KEYS` to protect the gateway beyond its default
loopback-only deployment, use one of those gateway client tokens in each client
configuration. An xAI API key is deliberately not accepted as a gateway client
token.

## Do my other projects need UniGrok namespace folders? {#no-project-namespace}

**Keywords:** unrelated project, namespace, agents, codex, grok folder, global mcp, switch project

No. Register `http://localhost:4765/mcp` once in the IDE's global or user
configuration, then switch Git projects normally. The stable UniGrok service
runs its own baked code and documentation; it does not require `.agents`,
`.codex`, `.gemini`, `.grok`, `.github`, or any other UniGrok file in a caller's
project. Namespace files in the UniGrok source repository exist for contributors
developing UniGrok itself.

## Can UniGrok automatically see the project open in my IDE? {#workspace-context-boundary}

**Keywords:** workspace, project files, filesystem, context, agent workspace_context, privacy

Not through MCP registration alone. The stable service is workspace-neutral and
cannot browse whichever folder an IDE currently has open. When Grok needs local
evidence, the IDE agent should send deliberately selected excerpts, diffs,
errors, or other text in the `agent` tool's optional `workspace_context` field,
with `workspace_label` when useful. This keeps project access explicit and
prevents one persistent service from silently inheriting every IDE's filesystem.

UniGrok contributors may instead run `docker compose -f
docker-compose.dev.yml up --build -d` on port 4766. That separate development
service mounts the UniGrok repository and enables its local file/git/test and
commit-memory workflows; it is not the globally registered stable service.

## What are the Grok phoneword mode ports? {#mode-dial-ports}

**Keywords:** dial plan, phoneword, auto port, fast port, thinking port, research port, mode alias

The canonical endpoint is `4765`, which spells **GROK** on a phone keypad. An
optional Compose overlay adds mode “speed dials” to the same service:

- `2886` (**AUTO**) → `auto`
- `3278` (**FAST**) → `fast`
- `7327` (**REAS**) → `reasoning`
- `8465` (**THNK**) → `thinking`
- `7724` (**RSCH**) → `research`

Enable them with `docker compose -f docker-compose.yml -f
docker-compose.dials.yml up --build -d`. They do not create separate agents,
databases, or session stores. The dial supplies a default only when the caller
omits `mode`; an explicit `agent.mode` always wins.

## Why are my sessions and telemetry separate in each IDE? {#client-id-sessions}

**Keywords:** x-client-id, session, telemetry, cursor, vscode, claude code

UniGrok uses the stable `X-Client-ID` header to namespace session names and
attribute telemetry. For example, a session named `main` from Cursor and VS
Code is stored independently as `cursor:main` and `vscode:main`. Keep the
recommended per-client IDs for isolation; use the same explicit client ID only
when you intentionally want clients to share the same session namespace.

## Why did a request use the API plane instead of the CLI plane? {#api-vs-cli-plane}

**Keywords:** api plane, cli plane, subscription, routing, grok cli, cost

UniGrok routes per request. Local service mode is CLI-first for compatible,
unpinned work because the authenticated Grok CLI uses the user's subscription.
The API plane remains authoritative for explicit API-model pins and API-native
thinking, vision, and multi-agent research capabilities.

Authenticate the running global Docker service once using the permission-gated
`setup_command` returned by `/runtimez` or `grok_mcp_status`; it repairs legacy
volume ownership, drops back to the unprivileged service uid, and starts device
auth without API-key variables. Do not authenticate separately inside caller
projects. UniGrok removes
`XAI_API_KEY` from CLI child processes so an API-backed CLI cannot masquerade
as the independent subscription plane. The final response and Control Center
show the selected `model`, `route`, `plane`, `cost_usd`, and latency. A request
choosing the API plane is not an error: model compatibility, requested
capabilities, and CLI readiness all influence routing.

CLI-first selection uses the model ids returned by the authenticated live
`grok models` probe. It never assumes a previously known subscription model
still exists; the current catalog's default is preferred for reasoning and its
composer model for coding.

## What should an IDE agent do when a credential plane is unavailable? {#credential-plane-actions}

**Keywords:** credentials, missing api key, cli auth, install cli, permission

On first connection, inspect `grok_mcp_discover_self.data.credential_planes`.
Each notice has a stable id, severity, whether it blocks model work, and a
bounded action. Prompt the user once per notice id and repeat only after the
reported state changes.

- CLI missing or unauthenticated: ask permission before installing/rebuilding
  the CLI or running its interactive device-auth command. Continue on API when
  available.
- API key missing while CLI is ready: prompt once without blocking compatible
  CLI work; block only when the requested capability requires API.
- Both unavailable: stop model execution and offer both actions.

Never ask the user to paste `XAI_API_KEY` into chat, the Control Center, or an
unrelated project. Offer to help configure it through a secure local editor or
prompt in the global UniGrok service `.env`, recreate the service, then verify
the new plane state. Team ids and management keys are advanced organization
billing/RAG settings, not prerequisites for ordinary usage or local telemetry.

## How do I see the model, route, plane, and cost for a request? {#request-metadata}

**Keywords:** cost, tokens, model, route, plane, telemetry, control center

Open the Control Center at `http://localhost:4765/ui/` after the gateway starts.
Its result panel shows the request status, tokens, cost in USD, latency, route,
and plane. MCP `agent` responses also carry structured execution metadata.

These values describe the gateway request that produced the response. A
zero-cost local FAQ lookup is documentation retrieval, not a model invocation.

The Usage & Telemetry tab does not combine API-key billing with SuperGrok CLI
activity. API spend is exact per response. CLI activity is tracked locally,
but xAI exposes no SuperGrok quota/spend API, so subscription cost is shown as
unknown/included rather than a misleading `$0`. Optional Management API
credentials add a separately labeled team-wide API comparison only.

## Which models are available on the CLI and API planes? {#models-by-plane}

**Keywords:** models tab, cli models, api models, model catalog, shared model id

Open the Control Center's **Models & Planes** tab. It queries
`grok_mcp_discover_self` with `include_models: true` and shows the authenticated
Grok CLI subscription catalog separately from the xAI developer API catalog.
Each side reports readiness, live-versus-fallback source, the CLI default, and
its usage-accounting boundary.

The same model id may appear on both sides. That duplication is intentional:
the model slug is not the credential plane. A pin selects a model while the
router still chooses a healthy compatible plane according to policy and
capability requirements. Headless clients can read the same structured truth
from `data.model_catalog` in the opt-in discovery response.

## Why does UniGrok use port 4765, and what if it is occupied? {#port-in-use}

**Keywords:** port, 4765, grok keypad, address in use, bind, docker compose

Port `4765` spells GROK, making the service memorable while avoiding the common
development port `8080`. If it is occupied, either stop the other local service
or set a different stable host port when
starting Compose. For example:

```bash
UNIGROK_PORT=9090 docker compose up --build -d
```

Then use `http://localhost:9090/mcp` and `http://localhost:9090/ui/`. When
running the HTTP server directly rather than through Docker Compose, set
`PORT=9090`; `UNIGROK_PORT` is not a supported variable.

## How do I check that the gateway and CLI plane are healthy? {#health-checks}

**Keywords:** healthz, readyz, health check, grok check, cli ready

Check the gateway health endpoint with:

```bash
curl -s http://localhost:4765/healthz
```

Use `/readyz` when you also need readiness checks for model authentication,
state storage, and SQLite. Inspect the non-secret CLI state with:

```bash
curl -s http://localhost:4765/runtimez
```

If it reports `needs_auth`, run the `setup_command` returned in the CLI-plane
status. It targets the running global container, so it works from any project
directory. `grok --check` is not a health probe; in the xAI CLI it enables a
prompt self-verification loop.

The gateway uses Streamable HTTP at `/mcp`; it does not expose the legacy `/sse`
endpoint.

## Why did auto mode choose this Grok model? {#model-selection-receipt}

**Keywords:** model selection, grok 4.5, auto mode, routing receipt, why, research model

Inspect the `routing` object returned by `agent`, or expand the matching row in
Control Center's **Recent Routing Receipts** panel. It records the capability
class, bounded prompt features, candidate models, evidence and catalog source,
explicit pin source, selected model, and failover reason without storing the
prompt itself.

Cold-start API defaults are `grok-4.5` for planning/vision and
`grok-build-0.1` for coding. On the preferred local CLI plane, UniGrok instead
uses the authenticated live catalog: its reported default for reasoning and a
live composer model for coding. Research chooses an available Grok 4.20
multi-agent API slug. Explicit `model` arguments and `UNIGROK_*_MODEL`
overrides always win. Fresh evaluation calibration or mature local telemetry
can promote a peer only when its success rate clears the 0.15 quality margin,
preventing small samples from making model selection flap.

## Docker started but UniGrok is not working. Where are the logs? {#docker-logs}

**Keywords:** docker, compose, logs, container, wsl2

Confirm Docker Desktop (or the Linux Docker daemon) is running, then rebuild the
local service if needed:

```bash
docker compose down
docker compose up --build -d
docker compose logs -f grok-mcp
```

The Compose service is named `grok-mcp`. On Windows, make sure Docker Desktop
has WSL2 integration enabled for the distribution that runs the checkout. Avoid
`docker compose down -v` for routine recovery because volume deletion is
unnecessary and can discard unrelated Docker-managed data.

## Can I expose UniGrok to my LAN or the internet? {#network-security}

**Keywords:** lan, network, remote, expose, security, api keys

By default Docker Compose publishes UniGrok only to `127.0.0.1`, so it is for
IDE clients on the same machine. Before deliberately exposing it beyond
loopback, configure `UNIGROK_API_KEYS` and add an `Authorization: Bearer`
gateway token to each client configuration. Keep the upstream `XAI_API_KEY`
server-side. See `SECURITY.md` before changing the bind or port publication.

## Does the Control Center receive my xAI API key? {#control-center-security}

**Keywords:** control center, browser, api key, secret, security

No. The browser Control Center talks to the local UniGrok gateway. The upstream
`XAI_API_KEY` stays in the server/container environment and is not returned in
agent responses, telemetry, browser payloads, or client MCP configuration.

## How do I reset a chat session or local state? {#reset-local-state}

**Keywords:** reset, clear, session, state, sqlite, chat history

For one stored conversation, use the local trusted workflow's
`clear_chat_history` tool with the target session name. Sessions are namespaced
by `X-Client-ID`, so use the client-prefixed name when appropriate.

For broader local state maintenance, first inspect the configured state location
and take a backup. Do not delete SQLite files or Docker volumes as a general
troubleshooting step. If you need to start over, use a new `UNIGROK_STATE_DIR`
or follow the maintenance guidance in the project documentation.
