---
name: using-unigrok
description: How to query xAI Grok through the UniGrok MCP gateway. Activate when the user says "@grok", asks to query Grok, wants a second-model opinion or peer review from Grok, or needs web/X search grounding.
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
- `session` (optional): a stable, project-qualified key such as
  `owner-repo:task` for multi-turn continuity. Do not reuse a generic session
  key across repositories.
- `workspace_context` (optional): deliberately selected excerpts, diffs, or
  errors when the task depends on the caller's project. The stable service
  cannot browse the IDE workspace automatically.
- `workspace_label` (optional): descriptive metadata for the supplied context.
  It does not isolate sessions; the project-qualified `session` key does.

Every result includes `response` plus metadata: `model`, `route`, `plane`
(`API` or `CLI`), `cost_usd`, `tokens`, `latency_sec`, and `citations` when
search grounding was used.

## Mode selection guidance

- Quick factual or single-file questions → `fast`.
- Design reviews, audits, multi-step analysis → `reasoning`.
- Tasks needing self-critique → `thinking`.
- Current-events or source-cited answers → `research` (uses web + X search).

## Cost awareness

Check `cost_usd` in each response. Session reuse preserves continuity and can
reduce repeated-context API input cost when caching hits; it does not guarantee
savings. The default `cli_first` policy prefers compatible, unpinned work on an
authenticated CLI subscription. Explicit plane selection should pair with
`fallback_policy="same_plane"` when the request must not cross into a different
credential or billing plane; `cross_plane` allows bounded failover. CLI
provider cost remains unavailable, so report local counts and estimated tokens,
not invented subscription cost or remaining provider quota.

## Safe onboarding behavior

- Treat `credential_planes` notices from status or an agent result as the
  source of truth. Ask before device authentication, installation, or secret
  configuration.
- Never request `XAI_API_KEY` in chat or write it into the caller's project.
- The stable service is workspace-neutral: do not assume MCP registration
  grants filesystem access. Use `workspace_context` or local file tools only
  when those tools are actually exposed in the current trusted
  contributor/stdio session.
- Translate provider and transport errors into one concrete next action for
  the user; do not require them to understand planes, MCP transport, or JSON-RPC.

## Endpoint

The shared gateway runs at `http://localhost:4765/mcp` (Streamable HTTP).
Health: `GET /healthz`. Browser Control Center: `http://localhost:4765/ui/`.
`X-Client-ID` is a caller-controlled attribution and session label beneath a
server-derived principal; it is not authentication. The default unauthenticated
loopback service derives the shared `http:anon` principal for one local
budget/security trust domain, so there is no cross-user isolation without
configured auth. Project-qualify every session key: a generic key can collide
across repositories when the same client label is reused.
