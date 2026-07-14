---
name: using-unigrok
description: Claude Code guidance for querying xAI Grok through the shared UniGrok MCP gateway. Activate when the user says "@grok", asks to query Grok, wants a Grok second opinion or peer review, or needs web/X search grounding.
---

# Using UniGrok from Claude Code

This is the Claude Code adaptation of the canonical gateway skill
([skills/using-unigrok/SKILL.md](../../../skills/using-unigrok/SKILL.md));
it adds only what is specific to Claude Code sessions.

## Tool resolution

The gateway's headline tool is `agent`, but its full Claude Code name depends
on how the MCP server was registered:

- `mcp__unigrok__agent` — connected through this repository's project
  `.mcp.json` (server name `unigrok`).
- `mcp__grok__agent` — connected through a user-scope registration named
  `grok` (the Codex-compatible name).

Prefer the project-controlled `unigrok` registration. `.claude/settings.json`
pre-allows its `agent`, `grok_mcp_status`, and `grok_mcp_discover_self` tools.
A user-owned `grok` alias is not controlled by this repository; verify that it
points to the expected UniGrok endpoint before approving or using its tools.

## Calling `agent`

- `prompt` (required); `mode` optional: `auto` (default), `fast`, `reasoning`,
  `thinking`, `research`.
- `workspace_context`: the stable service is workspace-neutral and cannot see
  the open folder — attach selected excerpts, `git diff` output, or errors
  yourself. An optional `workspace_label` is descriptive metadata only; it does
  not isolate sessions.
- `session`: pass a stable, project-qualified key such as `owner-repo:task`.
  The server namespaces it beneath a server-derived principal and the
  caller-controlled `X-Client-ID` label (`plugin` in this repository's current
  `.mcp.json`). A generic key can collide across repositories that reuse a
  common label such as `claude-code`. Reuse preserves continuity and can reduce
  repeated-context API input cost when caching hits, but savings are not
  guaranteed and CLI provider cost remains unavailable.
- `model`: pin only when asked; pins validate against the live catalog. An
  explicit `plane=cli|api` should pair with `fallback_policy="same_plane"`
  when billing planes must not cross.

Every result returns `response` plus `model`, `route`, `plane`, `why`,
`degraded`, `cost_usd`, `tokens`, `latency_sec`, and `citations` when search
grounding ran. Surface `cost_usd` when the user cares about spend.

## Claude Code patterns

- **Second opinion before handoff**: send the branch diff via
  `workspace_context` with `mode=reasoning` for a Grok peer review.
- **Long calls should not block the turn**: `research` and `thinking` runs can
  take minutes; wrap them in a background subagent (Agent tool) and keep
  working, then relay the result.
- **Health first**: on connection trouble, check `GET /healthz` on port 4765
  and `grok_mcp_status`; `8080` is container-internal and never reachable from
  the host.

## Registering in another repository

The gateway is machine-global; projects need no UniGrok files. Teammates run:

```bash
claude mcp add --transport http unigrok http://localhost:4765/mcp \
  --header "X-Client-ID: claude-code"
```

`X-Client-ID` is a caller-controlled attribution and session label, not an
authenticated identity. The server derives the principal; the default
unauthenticated loopback service derives the shared `http:anon` principal for
one local budget/security trust domain. There is no cross-user isolation without
configured auth. Because many repositories can reuse
`X-Client-ID: claude-code`, always project-qualify the `session` key;
`workspace_label` remains descriptive only. Control Center:
`http://localhost:4765/ui/`.

## Safety

- Never request `XAI_API_KEY` in chat or write it into any IDE config; the
  credential belongs to the server.
- Treat `credential_planes` notices from status or agent results as the source
  of truth; ask the user before device authentication, installation, or secret
  configuration.
- Translate provider and transport errors into one concrete next action; the
  user should not need to understand planes or JSON-RPC.


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

Do **not** invent multi-provider public chat tools. UniGrok's public `agent`
remains Grok-routed; other providers stay behind Grok-supervised adapters.

## Plan critique habit (opt-in)

When the user is about to see a multi-step Implementation Plan, prefer calling
UniGrok `agent` (`thinking` or `reasoning`) for a second opinion, then improve
the plan before presenting it **only when the user wants a Grok second opinion**
(including `@grok`). Do not silently spend metered API credits without consent.
Do not invent a second MCP port or Forge workflow for public installs. Public
path remains `http://localhost:4765/mcp` only.
