---
name: using-unigrok
description: How to query xAI Grok through the UniGrok MCP gateway. Activate when the user says "@grok", asks to query Grok, wants a second-model opinion or peer review from Grok, needs web/X search grounding, deferred research jobs, or Grok Imagine image/video generation.
---

# Using the UniGrok Grok Gateway

UniGrok exposes one headline MCP tool: `agent`. It self-routes across Grok
models and two billing planes, and returns structured metadata with every
answer.

## The `agent` tool

Call `agent` with:

- `prompt` (required): the task or question, with enough context to act on.
- `mode` (optional): `auto` (default), `fast` (single-turn, cheapest),
  `reasoning` (multi-step planner), `thinking` (reflected agent loop), or
  `research` (citation-grounded fanout).
- `model` (optional): pin a Grok model id such as `grok-4.5`; leave unset to
  let routing choose.
- `session` (optional): stable session name for multi-turn continuity.

Every result includes `response` plus metadata: `model`, `route`, `plane`
(`API` or `CLI`), `cost_usd`, `tokens`, `latency_sec`, and `citations` when
search grounding was used.

## Mode selection guidance

- Quick factual or single-file questions → `fast`.
- Design reviews, audits, multi-step analysis → `reasoning`.
- Tasks needing self-critique → `thinking`.
- Current-events or source-cited answers → `research` (uses web + X search).

## Cost awareness

Check `cost_usd` in each response. Session reuse lowers cost on follow-ups.
Requests may route to the CLI plane (subscription, ~$0 marginal) when the
gateway has an authenticated Grok CLI available; failures on the API plane
degrade gracefully to it.

## Endpoint

The shared gateway runs at `http://localhost:8080/mcp` (Streamable HTTP).
Health: `GET /healthz`. Browser Control Center: `http://localhost:8080/ui/`.
Send a stable `X-Client-ID` header so sessions and telemetry stay separated
per client.
