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
- **Deliver-or-fail opinions** (`reasoning`/`thinking`): by design the default
  `cross_plane` policy may recover a rejected non-answer on the *other* plane —
  a `thinking`/`reasoning` run that the reflection gate rejects can be answered
  by a CLI completion instead, returned with `finish_reason="fallback"` (this is
  intentional; see `tests/test_utils.py`). That recovery can be a weaker, non-
  reflected completion. When you want the API reasoning loop to **deliver or
  fail** rather than soften to a CLI recovery — e.g. a high-stakes peer review —
  pin `plane=api` with `fallback_policy="same_plane"`. Accept the tradeoff: you
  may get a hard error/empty result instead of a softer CLI answer. That is the
  point.

Every result returns `response` plus `model`, `route`, `plane`, `why`,
`degraded`, `cost_usd`, `tokens`, `latency_sec`, and `citations` when search
grounding ran. Surface `cost_usd` when the user cares about spend.

## Claude Code patterns

- **Second opinion before handoff**: send the branch diff via
  `workspace_context` with `mode=reasoning` for a Grok peer review. For a
  deliver-or-fail API opinion, add `plane=api` + `fallback_policy=same_plane`
  (see the `model`/plane note above).
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

## First-connect diagnostics (server + local)

On first connect in a session, call `grok_mcp_discover_self` and read
`data.bootstrap` + `data.request_context` before inventing setup steps.

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
- Stable `X-Client-ID` per IDE (for Claude Code: `claude-code`).
- No `XAI_API_KEY` (or other secrets) embedded in MCP JSON.
- Project `.mcp.json` (if any) is dual HTTP for UniGrok worktrees, never
  broken `unigrok-stdio` with `${PLUGIN_ROOT}`.
- Optional `using-unigrok` skill present; do not copy contributor `.agents`
  trees into foreign apps.

Then one cheap verification: `agent` with `mode=fast` or `grok_mcp_status`.

## Status language (vibe apps vs UniGrok product)

- **Foreign apps / stable MCP only:** Done / Blocked / plain English — not
  multi-agent **Ready for supervisor** land radio unless shipping UniGrok itself.
- When `can_mutate_workspace` / `can_use_swarm` are false, contributor workflows
  are disabled (no land, Forge, Swarm, or product rehydrate).

## Safety

- Never request `XAI_API_KEY` in chat or write it into any IDE config; the
  credential belongs to the server.
- Treat `credential_planes` notices from status or agent results as the source
  of truth; ask the user before device authentication, installation, or secret
  configuration.
- Translate provider and transport errors into one concrete next action; the
  user should not need to understand planes or JSON-RPC.


## Parallel ship (contributor, private)

Dual-lane shipping process lives in private `djtelicloud/unigrok-intelligence`.
Public product installs never require it.


## Plan critique habit (opt-in)

When the user is about to see a multi-step Implementation Plan, prefer calling
UniGrok `agent` (`thinking` or `reasoning`) for a second opinion, then improve
the plan before presenting it **only when the user wants a Grok second opinion**
(including `@grok`). Do not silently spend metered API credits without consent.
Do not invent a second MCP port or Forge workflow for public installs. Public
path remains `http://localhost:4765/mcp` only.
