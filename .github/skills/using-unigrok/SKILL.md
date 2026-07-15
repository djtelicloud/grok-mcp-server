---
name: using-unigrok
description: VS Code Copilot guidance for querying xAI Grok through the shared UniGrok MCP gateway. Activate when the user says "@grok", asks to query Grok, requests a Grok second opinion/peer review, wants web/X grounded research, or needs cross-repo UniGrok setup help.
---

# Using UniGrok from VS Code Copilot

This is the Copilot/VS Code adaptation of the canonical gateway skill at
`skills/using-unigrok/SKILL.md`. Use it when working in this repository and
also when helping users in other repositories that connect to the same local
UniGrok MCP service.

## Tool resolution

The core tool is `agent`, but the namespaced tool name depends on MCP server
registration:

- `mcp__unigrok__agent` when the server name is `unigrok` (common workspace
  config).
- `mcp__grok__agent` when user scope registers the same endpoint as `grok`.

Use whichever namespace exists in the active tool list.

## Calling pattern

- `prompt` (required): the exact task/question.
- `mode` (optional): `auto`, `fast`, `reasoning`, `thinking`, `research`.
- `session` (optional): stable per-task session key (reuse for follow-ups).
- `workspace_context` (optional): selected code snippets, diffs, logs, or
  errors from the caller's repo.
- `workspace_label` (optional): human-readable repo/project name.
- `model` (optional): pin only when asked.
- `plane` and `fallback_policy` (optional): use
  `fallback_policy="same_plane"` when the request must not cross billing
  planes.

The stable service is workspace-neutral and cannot browse the caller's files
automatically; attach only deliberate context via `workspace_context`.

## Response handling

Surface both `response` and key metadata when relevant:

- `model`, `route`, `plane`, `why`, `degraded`
- `cost_usd`, `tokens`, `latency_sec`
- `citations` (when research grounding is used)

If users are cost-sensitive, explicitly report `cost_usd` and encourage session
reuse for follow-up questions.

## VS Code team distribution

- **Workspace-shared skill (git-backed):**
  `.github/skills/using-unigrok/SKILL.md`
- **Personal user-level skill (not in git):**
  `~/.copilot/skills/using-unigrok/SKILL.md`

Use the workspace path for team defaults. Use the personal path for individual
defaults across unrelated repositories.

Do not duplicate this skill under a repository-root `.copilot/skills`
directory; that is not a default project discovery path. VS Code can opt into
additional paths through `chat.agentSkillsLocations` when a deliberate
nonstandard location is required.

## MCP endpoint and identity

Default endpoint: `http://localhost:4765/mcp`

Every IDE config should send a stable `X-Client-ID` (for example `vscode`) so
session namespaces and telemetry remain separated by client.

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
- Stable `X-Client-ID` per IDE (for VS Code: `vscode`).
- No `XAI_API_KEY` (or other secrets) embedded in MCP JSON.
- Project `.mcp.json` (if any) is dual HTTP for UniGrok worktrees, never
  broken `unigrok-stdio` with `${PLUGIN_ROOT}`.
- Optional `using-unigrok` skill present; do not copy contributor `.agents`
  trees into foreign apps.

Then one cheap verification: `agent` with `mode=fast` or `grok_mcp_status`.

## Safety and credential boundary

- Never ask users to paste `XAI_API_KEY` into IDE MCP config; credentials live
  server-side.
- Treat `credential_planes` status in tool output as source of truth.
- On failures, return one concrete next action (for example check `/healthz`,
  authenticate CLI plane, or verify MCP registration).


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
