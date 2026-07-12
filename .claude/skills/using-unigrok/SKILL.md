---
name: using-unigrok
description: Claude Code guidance for querying xAI Grok through the shared UniGrok MCP gateway. Activate when the user says "@grok", asks to query Grok, wants a Grok second opinion or peer review, needs web/X search grounding, or wants deferred research or Grok Imagine media.
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

Use whichever is connected in the session. `.claude/settings.json` pre-allows
`agent`, `grok_mcp_status`, and `grok_mcp_discover_self` under both namespaces,
so calls should not raise permission prompts.

## Calling `agent`

- `prompt` (required); `mode` optional: `auto` (default), `fast`, `reasoning`,
  `thinking`, `research`.
- `workspace_context`: the stable service is workspace-neutral and cannot see
  the open folder — attach selected excerpts, `git diff` output, or errors
  yourself, with an optional `workspace_label`.
- `session`: pass a stable per-task name; the server namespaces it by caller
  (`claude-code:<name>`), and reuse lowers cost on follow-ups.
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

`X-Client-ID` attributes telemetry, budgets, and `/metrics` per IDE and keeps
session namespaces separate. Control Center: `http://localhost:4765/ui/`.

## Safety

- Never request `XAI_API_KEY` in chat or write it into any IDE config; the
  credential belongs to the server.
- Treat `credential_planes` notices from status or agent results as the source
  of truth; ask the user before device authentication, installation, or secret
  configuration.
- Translate provider and transport errors into one concrete next action; the
  user should not need to understand planes or JSON-RPC.
