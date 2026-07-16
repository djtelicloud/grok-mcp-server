# `.cursor/` — Cursor project namespace

This folder is the **Cursor-only** project surface for UniGrok.
New Cursor users should open this repo in Cursor and keep these files —
do **not** copy repo-root [`.mcp.json`](../.mcp.json) into Cursor (that file
is the VS Code / Copilot path and uses `vscode` / `vscode-forge` labels).

## Root files (what you should see)

| Path | Purpose |
|------|---------|
| [`mcp.json`](mcp.json) | UniGrok MCP with `X-Client-ID: cursor` / `cursor-forge` |
| [`rules/`](rules/) | Always-on agent law (Automations, routing, Canvas, radio, …) |
| [`hooks.json`](hooks.json) + [`hooks/`](hooks/) | Optional session/MCP tips (lands via Ready hooks PR when present) |
| [`README.md`](README.md) | This guide |

Healthy Control Center / telemetry label looks like `http:anon|cursor` — never
bare `http:anon`, and never `vscode` from a Cursor session.

## First-run checklist

1. Start UniGrok (`docker compose up` or local HTTP on `:4765`).
2. Open **Settings → MCP** and enable `unigrok` (and `unigrok-forge` if you use
   contributor `:4766`).
3. In chat, call `grok_mcp_discover_self` or a cheap `agent` probe — confirm
   `client_id` normalizes to `cursor`.
4. Prefer **Canvas** for rich UI beside chat; Mermaid for small diagrams; never
   raw HTML widgets in the bubble. See `rules/cursor-canvas-artifacts.mdc`.
5. Sponsor language: **Ready / Live / Blocked** — see
   `rules/cursor-human-radio.mdc`. **Push ⇒ draft PR** —
   `rules/cursor-push-means-draft-pr.mdc`.

## Do not put here

- Secrets, API keys, OAuth tokens, or home MCP overrides with credentials
- VS Code `vscode` client ids (those stay in repo-root `.mcp.json`)
- Managed Canvas sources (`~/.cursor/projects/.../canvases/*.canvas.tsx`) —
  those are IDE-local, not git

## More detail

Full IDE setup (including home `~/.cursor/mcp.json` as an alternative):
[docs/ide-setup.md](../docs/ide-setup.md) → **Cursor**.
