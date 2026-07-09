# Codex Browser And Chrome Control

Use this when Codex needs Browser or Chrome behavior that is specific to the
Codex desktop app.

## Route

- Use the Browser plugin for local targets such as `localhost`, `127.0.0.1`,
  `::1`, and `file://` URLs.
- Use the Chrome plugin when the task depends on existing Chrome state, logged
  in sessions, or Chrome extensions.
- Use `node_repl` JavaScript execution to drive Browser or Chrome control.

## Node REPL Rules

- Use `mcp__node_repl.js` when instructions say `node_repl`.
- Use dynamic imports such as `await import("playwright")`.
- Use `nodeRepl.cwd`, `nodeRepl.homeDir`, and `nodeRepl.tmpDir` for host paths.
- Use `nodeRepl.write(...)` for machine-readable text.
- Use `await nodeRepl.emitImage(...)` to return screenshots or rendered images
  into Codex.
- Prefer fresh names or reusable `var` bindings because top-level bindings
  persist between calls.
- Use `js_reset` only when name conflicts cannot be resolved cleanly.

## Verification Pattern

For UI or local web work, Codex should capture enough evidence to support the
answer:

- target URL or file path
- viewport or browser context
- screenshot or textual observation
- errors from the page console or network layer when relevant

