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
- **Response**: A formatted text report containing version, runtime model mapping, active session count, total daily cost, caller budgets, and circuit breaker status.

### 2. Prometheus Endpoint (`/metrics`)
For HTTP gateways, the server exposes a `/metrics` route (Prometheus text exposition format) that tracks:
- `unigrok_request_total`: Request counter labelled by status and model.
- `unigrok_tokens_total`: Counter for input and output token consumption.
- `unigrok_latency_seconds`: Latency tracking.
- `unigrok_plane_cost_usd_total`: Billing tracker mapped by plane.
- `unigrok_caller_cost_usd_total`: Budget metrics mapped by client/caller identifier.

## Circuit Breakers & Failover

The gateway maintains a circuit breaker status to prevent flooding xAI APIs during outages:
- If error rates spike, the circuit trips to **Open** state.
- During Open state, new calls automatically failover to local CLI execution or return cached fallbacks immediately.
- The status resets to **Closed** after error rates recover.

## Billing Spending Budgets
Spending limits are enforced per-caller based on client authorization bearer tokens or MCP client info names. Once a caller reaches their daily limit (defined in `UNIGROK_CALLER_BUDGETS`), the gateway throws a budget exceeded exception immediately, protecting operators from run-away agent loops.
