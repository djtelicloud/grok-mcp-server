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
- `model` (optional): pin a **live catalog** Grok model id such as `grok-4.5`
  or API coding slug `grok-build-0.1`; leave unset to let routing choose.
  Never invent ids from product names. The Grok Build IDE product is not the
  same identity as `grok-build-0.1`. Prefer `plane` + `fallback_policy` when
  the billing plane must not cross (`cli` vs `api`, `cli_first` default).
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

## Parallel ship (contributor, private)

Dual-lane product+intelligence shipping process lives in the **private**
repository `djtelicloud/unigrok-intelligence` (playbooks + `grok-parallel-ship`
skill). Public product installs never require it.

Do **not** invent multi-provider public chat tools. UniGrok's public `agent`
remains Grok-routed; other providers stay behind Grok-supervised adapters.

## Public intelligence packs

New clones start smarter via **reviewed recipes** under
`docs/public-intelligence/` (not private memory dumps). Read the latest pack
in `docs/public-intelligence/packs/` when onboarding agents. Contributors:
after work is Live, ask once whether to promote a scrubbed pack/skill update.
Never continuous-sync private intelligence into public.


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

- On first connect in a session, call `grok_mcp_discover_self` and read
  `data.bootstrap` + `data.request_context` before inventing setup steps.
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

## First-connect diagnostics (server + local)

**Server (always available via MCP):**

1. Call `grok_mcp_discover_self`.
2. Honor `data.bootstrap.status` (`OK` / `WARN` / `ERR`) and gates
   (`can_chat`, `can_spend_api`, `can_mutate_workspace`, `can_use_swarm`).
3. Read `data.request_context`: surface (`stable_core` / `contributor_forge` /
   `mode_dial`), `client_id_present`, optional Host port / mode dial.
4. Prompt once per `credential_planes` notice id; follow
   `data.bootstrap.next_actions` when present.
5. Optional: `include_models: true` when model routing matters.

**Local IDE audit (only with user permission; report only):**

UniGrok cannot read global IDE settings over HTTP. With consent, use local tools
to check and **report** (never rewrite without explicit permission; never print
secret values):

- User MCP configs point daily chat at `http://localhost:4765/mcp`.
- Stable `X-Client-ID` per IDE (claude-code, vscode, codex, cursor, antigravity).
- No `XAI_API_KEY` (or other secrets) embedded in MCP JSON.
- Project `.mcp.json` (if any) is dual HTTP for UniGrok worktrees, never
  broken `unigrok-stdio` with `${PLUGIN_ROOT}`.
- Optional `using-unigrok` skill present; do not copy contributor `.agents`
  trees into foreign apps.
- Unique leverage: which planes are ready, whether Forge tools appear only when
  intentionally connected to the contributor surface, mode dials if enabled.

Then one cheap verification: `agent` with `mode=fast` or `grok_mcp_status`.

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
