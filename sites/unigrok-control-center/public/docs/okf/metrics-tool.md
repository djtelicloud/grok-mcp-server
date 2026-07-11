---
okf_version: "0.1"
title: "Observability & Metrics"
type: "topic"
description: "UniGrok gateway diagnostics, Prometheus http endpoint, circuit breakers, and status tools."
---

# Observability & Metrics

UniGrok is built for zero-trust swarm environments, providing rich operational telemetry, billing budget controls, and circuit breakers.

## Telemetry Observability

### 1. Stdio Status Tool (`grok_mcp_status`)
For stdio MCP hosts, querying gateway metrics can be done directly through the tool:
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

Rows created before v0.5.0 naturally have no receipt. They remain valid for
cost, latency, success, model, caller, token, and plane aggregates, while
`data_quality.routing_receipt_rows` states the exact explainability coverage.

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

The gateway maintains a circuit breaker status to prevent flooding xAI APIs during outages:
- If error rates spike, the circuit trips to **Open** state.
- During Open state, new calls automatically failover to local CLI execution or return cached fallbacks immediately.
- The status resets to **Closed** after error rates recover.

## Billing Spending Budgets
Spending limits are enforced per-caller based on client authorization bearer tokens or MCP client info names. Once a caller reaches their daily limit (defined in `UNIGROK_CALLER_BUDGETS`), the gateway throws a budget exceeded exception immediately, protecting operators from run-away agent loops.
