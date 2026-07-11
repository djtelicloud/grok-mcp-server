# Codex Project Control

This namespace is for Codex desktop only. Keep general agent rules in
`.agents/AGENTS.md`; keep Grok adapter prompts and model profiles in `.grok/`.

## Purpose

`.codex/` describes how Codex should use Codex app APIs and installed Codex
plugins while working on UniGrok MCP. It is not a global Codex configuration
file and does not override `~/.codex/config.toml`.

Use these files when a task needs one of the Codex-only surfaces:

- Codex project threads, forks, handoff, thread titles, pinning, archiving, or
  follow-up prompts.
- Codex automations or thread heartbeats.
- Codex final-response directives.
- Codex Browser or Chrome control through `node_repl`.
- Codex Computer Use for macOS app UI.
- Chronicle or Codex memory hints.
- Codex OpenAI Platform API key setup.
- Codex plugin routing through `tool_search`.
- Codex extraction of useful knowledge from another provider namespace such as
  `.gemini/`, translated into Codex app/tool routes without copying provider
  settings.

## Source Files

- `manifest.json` is the project-local Codex capability manifest.
- `intelligence/codex-intelligence.json` maps task types to Codex tool routes.
- `threads/registry.json` defines Codex thread archetypes and lifecycle actions.
- `automations/*.json` are templates for `automation_update`.
- `mcp/grok-routing.json` describes Codex-to-UniGrok MCP routing.
- `plugins/capabilities.json` lists installed Codex plugins and safe use cases.
- `directives.md` documents Codex app response directives.
- `memory/context.md` provides durable project identity and risk hints.
- `memory/active-work.md` is the required cross-chat handoff for the latest
  unfinished or recently completed Codex-owned work.

## Boundaries

- Do not add generic coding standards, universal git etiquette, or general
  repo architecture to `.codex/`; those belong in `.agents/` or project docs.
- Do not store secrets, copied user config, auth state, tokens, or API keys.
- Do not add `.codex-plugin/` unless this repository is packaging a real Codex
  plugin.
- Treat all machine-readable files here as advisory project metadata unless
  the Codex app explicitly documents a loader for them.
- Do not copy another provider's namespace config directly. Extract only the
  project risk knowledge and re-express it through Codex APIs, plugins, MCP
  routes, and validation files.

## Implementation Completion Gate

- Perform implementation in a `codex/*` task worktree, never directly in the
  shared checked-out `main` folder.
- Commit the intended changes and run `./scripts/land`. Do not manually merge
  and do not tell the user work is complete until it prints
  `LANDED TO MAIN: <sha>`.
- A test pass or task-branch commit by itself is not completion. On failure,
  report `NOT LANDED: <specific blocker>` and continue when agent-resolvable.
- Do not remove the worktree after landing; another open Codex window or IDE
  may still reference it. Remote publication is a separate user-requested task.
- For implementation, debugging, architecture, or review, use the tracked
  `unigrok-workspace-memory` skill. Supply the Codex worktree's own full HEAD
  to recall; after landing, record one concise verified outcome against the
  SHA printed by `scripts/land`.
