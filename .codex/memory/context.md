# Codex Memory Seed

Use this as a compact Codex memory hint for UniGrok MCP. Do not treat it as
proof; verify current files before editing.

Every new Codex chat must also read `active-work.md`. Contributor-memory MCP
recall supplements that project-local handoff; it does not replace it.

- This repository is a UniGrok MCP server exposed through Codex as a trusted
  local project.
- `.codex/` is Codex desktop control metadata only.
- `.agents/` remains the cross-agent instruction namespace.
- `.grok/` remains Grok adapter prompts, profiles, and evolution material.
- In this Codex IDE, UniGrok MCP is reached through the public
  `mcp__grok.agent` tool. Do not assume stdio-only helper tools such as
  `grok_agent`, `chat`, `git_status`, or `grok_mcp_status` are available
  unless the live tool list exposes them.
- Codex-specific work may use app thread APIs, automations, Browser/Chrome via
  `node_repl`, Computer Use, Chronicle, OpenAI Platform key setup, GitHub
  connector tools, and final-response directives.
- Keep secrets and copied user-level Codex config out of the repository.
- When learning from `.gemini/`, extract durable project risk knowledge only:
  database concurrency, telemetry redaction, secret scanning, Grok MCP routing,
  and verification expectations. Do not copy Antigravity paths or settings.
- For UniGrok storage changes, remember the high-risk area is
  `GrokSessionStore` with async `aiosqlite`, write locking, and WAL/close
  behavior.
