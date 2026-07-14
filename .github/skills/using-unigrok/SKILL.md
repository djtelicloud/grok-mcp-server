---
name: using-unigrok
description: How to query xAI Grok through the UniGrok MCP gateway. Activate when the user says "@grok", asks to query Grok, wants a second-model opinion or peer review from Grok, or needs web/X search grounding.
---

# Using the UniGrok Grok Gateway

UniGrok exposes one headline MCP tool: `agent`. It self-routes across Grok
models and two billing planes, and returns structured metadata with every
answer. **Primary chat path for every project is IDE → UniGrok MCP**. The local
browser UI is an optional trusted-loopback test/control surface whose agent
playground can invoke providers and spend metered credits.

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

## Parallel ship (product + intelligence)

When the operator wants **both** public product work and intelligence/research
without midwifing git:

1. Split **Lane P** (product) and **Lane I** (intelligence) with path isolation.
2. Route UniGrok modes per lane (P: fast/reasoning/thinking; I: research/thinking).
3. Put coordination on the git DAG: agent-prefixed branch, draft PR, exact head,
   verification notes — so Codex can land without human git chat.
4. Prefer CLI plane for free-compatible critique; use API + same_plane when
   Deep-Think or hosted twin work requires it.
5. Full playbook: `.grok/playbooks/parallel-ship-dag.md` and skill
   `grok-parallel-ship` (contributor repo only).

Do **not** invent multi-provider public chat tools. UniGrok’s public `agent`
remains Grok-routed; other providers stay behind Grok-supervised adapters.

## Plan critique habit (opt-in)

When the user is about to see a multi-step **Implementation Plan**, prefer:

1. Call UniGrok `agent` (`thinking` or `reasoning`) with the draft plan.
2. Incorporate feedback silently.
3. Only then present the improved plan to the user.

Do this when the user wants a Grok second opinion (including `@grok`). Do **not**
silently spend metered API credits without consent. Do **not** auto-generate
skill trees into foreign projects without permission. Never copy the UniGrok
repository’s contributor `.agents` tree into the user’s other apps.

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
- Public product path is `http://localhost:4765/mcp` only. Do not invent a
  second port, Forge, Swarm, or land workflow for ordinary end-user installs.

## Endpoint

The shared gateway runs at `http://localhost:4765/mcp` (Streamable HTTP).
Health: `GET /healthz`. Optional local Core UI: `http://localhost:4765/ui/`
(trusted machine-owner test/control surface with a provider-calling agent
playground). `X-Client-ID` is a caller-controlled
attribution and session label beneath a server-derived principal; it is not
authentication. The default unauthenticated loopback service derives the shared
`http:anon` principal for one local budget/security trust domain, so there is
no cross-user isolation without configured auth. Project-qualify every session
key: a generic key can collide across repositories when the same client label
is reused.
