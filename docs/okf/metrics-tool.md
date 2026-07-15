---
okf_version: "0.1"
title: "Observability & Metrics"
type: "topic"
description: "UniGrok gateway diagnostics, Prometheus http endpoint, circuit breakers, and status tools."
---

# Observability & Metrics

UniGrok is built for zero-trust swarm environments, providing rich operational telemetry, billing budget controls, and circuit breakers.

> **Surface scope:** `grok_mcp_status` is available on stable HTTP, Forge HTTP,
> and trusted stdio. `/metrics` is an HTTP route, not an MCP tool. Remote
> access requires the `unigrok:status` OAuth scope or a configured static
> gateway token; verified local operator access follows the loopback boundary.

## Telemetry Observability

> [!IMPORTANT]
> **Glossary: Process Hydration vs Intelligence Rehydrate**
> - **Process / Telemetry Hydration**: When the server restarts, metrics (like caller budgets and semantic evaluator spend) are "hydrated" (recovered) from the durable `grok_sessions.db` to ensure honest limits and observability.
> - **Session Rehydrate**: The act of an agent reading git/disk to recover task intelligence.
> - **Hydration Lanes**: Disposable `.worktrees/` used for isolation.
> Only **Process Hydration** relates to UniGrok telemetry.

### 1. MCP Status Tool (`grok_mcp_status`)
Query gateway metrics through the MCP tool on any surface where live
`tools/list` exposes it:
- `view=text` (default) returns the human-readable operational report.
- `view=json` returns the stable structured usage ledger consumed by the Control Center: today/lifetime summaries, per-plane/model/caller activity, data coverage, circuit breakers, and billing-source metadata.

### 2. Prometheus Endpoint (`/metrics`)
For HTTP gateways, `/metrics` returns JSON by default and Prometheus text with
`?format=prometheus`. Existing plane/caller/runtime families remain stable; the
JSON payload additionally carries the same structured usage ledger used by the
MCP UI.

## Routing Receipts

New unified-agent telemetry rows include a versioned `routing` object. It is
prompt-free and bounded: selected model, capability class, model candidates,
feature bucket/hash, catalog source, evidence source, explicit pin source, and
failover facts. JSON metrics expose the newest receipts under
`usage.<period>.recent_routes`, plus aggregate `route_classes` and
`selection_reasons`. The UI renders these values directly; it never infers why
a model ran from a model name or dollar amount.

Rows created before routing receipts remain valid for operational fields such
as cost, latency, model, caller, token, and plane. They do not become explained
routes retroactively; `data_quality.routing_receipt_rows` states the exact
coverage.

Outcome truth is tri-state: `success=1` requires an explicit verifier;
`success=0` records an explicit gateway failure or verifier-established
failure; and `NULL` means the completion remains unverified. Success rates use
the non-null outcomes as the denominator and remain `null` when no such outcome
exists. Schema v14 cleared unsupported historical positive labels, so old
completion text is never treated as semantic success merely because a
transport returned normally.

## Billing Truth

- API calls record xAI's exact per-response billed cost and provider token
  counts. No price-table estimate or later account lookup is needed.
- CLI subscription calls record locally observed request count, success,
  latency, model, and locally estimated tokens. Per-request dollar cost and
  remaining SuperGrok quota are unavailable from xAI and are represented as
  unknown, not zero.
- An advanced, optional organization-wide API usage comparison may be enabled
  by operators. Ordinary users do not need a team id or management key: local
  API cost and CLI activity tracking work without them. Organization totals
  may include API traffic outside UniGrok and never change CLI statistics.

## Credential-plane status

`grok_mcp_discover_self`, `grok_mcp_status(view="json")`, `/runtimez`, and
public `agent` results share a versioned `credential_planes` object. It reports
the CLI-first preference, effective plane, non-secret readiness, local-usage
coverage, and bounded repair actions. Agents must ask the user before running
installation, device-auth, or secret-configuration actions and suppress repeat
prompts until the notice id changes.

## Circuit Breakers & Failover

The gateway maintains per-model circuit breakers to prevent repeated xAI API
calls during outages:
- If error rates spike, the circuit trips to **Open** state.
- During Open state, the selected API model is blocked. A request may use the
  CLI plane only when its requested plane, model compatibility, and
  `fallback_policy` permit that crossing; otherwise it fails explicitly.
- The status resets to **Closed** after error rates recover.

## Billing Spending Budgets
Spending limits are enforced against the authenticated principal: OAuth
subject remotely, static-key alias for keyed gateways, and MCP client identity
only as the stdio fallback. `X-Client-ID` remains a reporting/session label and
cannot change an OAuth subject's budget. Once a matching principal reaches its
daily `UNIGROK_CALLER_BUDGETS` limit, model work fails before execution.
