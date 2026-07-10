---
okf_version: "0.1"
faq_schema_version: "1"
source_version: "0.4.1"
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
      "url": "http://localhost:8080/mcp",
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
connects to the local MCP endpoint at `http://localhost:8080/mcp`; do not paste
the upstream xAI key into Cursor, Claude Code, VS Code, Codex, or Claude
Desktop configurations.

If you configure `UNIGROK_API_KEYS` to protect the gateway beyond its default
loopback-only deployment, use one of those gateway client tokens in each client
configuration. An xAI API key is deliberately not accepted as a gateway client
token.

## Why are my sessions and telemetry separate in each IDE? {#client-id-sessions}

**Keywords:** x-client-id, session, telemetry, cursor, vscode, claude code

UniGrok uses the stable `X-Client-ID` header to namespace session names and
attribute telemetry. For example, a session named `main` from Cursor and VS
Code is stored independently as `cursor:main` and `vscode:main`. Keep the
recommended per-client IDs for isolation; use the same explicit client ID only
when you intentionally want clients to share the same session namespace.

## Why did a request use the API plane instead of the CLI plane? {#api-vs-cli-plane}

**Keywords:** api plane, cli plane, subscription, routing, grok cli, cost

UniGrok routes per request. The API plane uses `XAI_API_KEY` and supports the
full API-backed capability set. The CLI plane uses an authenticated local Grok
CLI subscription when its binary and OAuth session are available; it provides
eligible CLI models and can be selected for cost-saving or API-failure fallback.

Use `grok --check` to verify the local CLI is ready. The final response and the
Control Center show the selected `model`, `route`, `plane`, `cost_usd`, and
latency. A request choosing the API plane is not an error: model compatibility,
requested capabilities, and CLI readiness all influence routing.

## How do I see the model, route, plane, and cost for a request? {#request-metadata}

**Keywords:** cost, tokens, model, route, plane, telemetry, control center

Open the Control Center at `http://localhost:8080/ui/` after the gateway starts.
Its result panel shows the request status, tokens, cost in USD, latency, route,
and plane. MCP `agent` responses also carry structured execution metadata.

These values describe the gateway request that produced the response. A
zero-cost local FAQ lookup is documentation retrieval, not a model invocation.

## Port 8080 is already in use. What should I change? {#port-in-use}

**Keywords:** port, 8080, address in use, bind, docker compose

Either stop the other local service or change UniGrok's host-side Compose port
mapping in `docker-compose.yml`. For example, change the published mapping to:

```yaml
ports:
  - "127.0.0.1:9090:8080"
```

Then use `http://localhost:9090/mcp` and `http://localhost:9090/ui/`. When
running the HTTP server directly rather than through Docker Compose, set
`PORT=9090`; `UNIGROK_PORT` is not a supported variable.

## How do I check that the gateway and CLI plane are healthy? {#health-checks}

**Keywords:** healthz, readyz, health check, grok check, cli ready

Check the gateway health endpoint with:

```bash
curl -s http://localhost:8080/healthz
```

Use `/readyz` when you also need readiness checks for model authentication,
state storage, and SQLite. Check CLI-plane readiness with:

```bash
grok --check
```

The gateway uses Streamable HTTP at `/mcp`; it does not expose the legacy `/sse`
endpoint.

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
