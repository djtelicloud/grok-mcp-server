---
okf_version: "0.1"
title: "VS Code Copilot Agent Playbook"
type: "topic"
description: "How GitHub Copilot in VS Code should call UniGrok agent, package workspace_context, choose modes and planes, and self-verify."
---

# VS Code Copilot Agent Playbook

This playbook turns UniGrok's stable HTTP contract into repeatable habits for
GitHub Copilot in VS Code.

## Superpowers to lean on

- Multi-file Edit and Agent work paired with terminal verification.
- MCP `agent` on stable `:4765` with `X-Client-ID: vscode` for ordinary work.
- Forge `:4766` with `X-Client-ID: vscode-forge` only while developing UniGrok
  itself and needing repository-mounted tools, workspace memory, or Swarm.
- Explicit `workspace_context` plus `workspace_label`, because the stable lane
  is workspace-neutral and cannot browse the open folder on its own.
- Structured result metadata such as `cost_usd`, `plane`, `routing`,
  `credentials.notices`, and `finish_reason`.

## Mandatory caller contract

1. Configure `.vscode/mcp.json` or the VS Code user MCP config with stable
   `X-Client-ID` headers. Never place `XAI_API_KEY` in IDE config.
2. On first connect, degraded results, or credential prompts, call discovery or
   status and surface each `credentials.notices` id once. Ask before any
   install or authentication action. Never request the key in chat.
3. For non-trivial tasks, deliberately pass evidence such as diffs, errors, key
   files, or test output through `workspace_context` and optionally
   `workspace_label`. Do not assume the gateway can see the current workspace.
4. Prefer the service defaults under `cli_first`. Use
   `fallback_policy="same_plane"` unless the user explicitly authorizes
   cross-plane recovery.
5. After every `agent` call, inspect `plane`, `cost_usd`, `routing`, and
   `credentials.notices` before escalating the mode or applying edits.

## Mode map

- Quick Ask or narrow follow-up: `mode="fast"` or default `auto`.
- Multi-file Edit or local coding: `mode="auto"` or `mode="reasoning"` with
  rich `workspace_context`.
- Deep planning, architecture, or hard bugs: `mode="reasoning"` or
  `mode="thinking"`.
- Broad research or multi-source synthesis: `mode="research"`.
- Explicit model pins stay on their declared plane; never assume CLI and API
  catalogs are interchangeable.

## Self-verification loop

- Run targeted `pytest` or the repo's existing verification commands after
  edits.
- Use status and metrics tools for session checks, plane visibility, and
  `cost_usd` review.
- Prefer small, auditable changes and avoid other brands' owned surfaces when a
  non-colliding lane exists.

## When to use Forge

Use Forge only for UniGrok repository work that needs mounted workspace tools,
workspace memory, or Swarm. Day-to-day use across other projects should stay on
stable `http://localhost:4765/mcp`.
