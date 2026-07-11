# Workspace Rules

## Grok MCP Integration Rules
- **Shared MCP Endpoint**: The host-facing shared service endpoint is `http://localhost:4765/mcp` (Streamable HTTP). Port `8080` is container-internal only. Start it with `docker compose up --build -d` from the primary checkout unless the user explicitly asks for stdio mode.
- **Per-Agent Identity**: Every IDE/agent config should send `X-Client-ID` with a stable value such as `codex`, `claude-code`, `vscode`, or `antigravity`. This attributes telemetry and keeps sessions separate.
- **Credentials Boundary**: The xAI API key belongs to the running server/container environment (`XAI_API_KEY`), not to each IDE client. Do not ask the user to paste the xAI key into IDE MCP configs. If `UNIGROK_API_KEYS` is configured, IDE clients additionally need `Authorization: Bearer <client-token>`.
- **Grok Mentions**: Whenever the user mentions "@grok", "grok", or explicitly asks to query Grok, call the shared UniGrok MCP `agent` tool when it is available rather than answering directly using your own model weights or context.
- **Code Peer Reviews**: Whenever the user asks to peer review code, audit architectural files, or perform quality checks in this repository, invoke the shared UniGrok MCP `agent` tool for Grok's direct feedback when the MCP service is available.
- **Operational Source of Truth**: For installation snippets and IDE-specific config, use `docs/ide-setup.md`. For browser-based manual testing, open `http://localhost:4765/ui/`.

## Multi-Agent Git Coordination
- **Shared Main Checkout**: This repository may be opened by several local IDE agents at once. The primary checkout should stay on `main` and represent the latest integrated local development state.
- **Do Not Branch-Switch the Shared Folder**: Agents must not switch branches or perform experimental edits in the shared `main` checkout when other IDEs may be using it. Branch switching changes the files visible to every agent using this folder.
- **Use Per-Agent Worktrees**: Every implementation runs in an agent-prefixed task worktree such as `codex/task-name`, `claude/task-name`, `gemini/task-name`, or `grok/task-name`. Never edit implementation files directly in the shared `main` checkout.
- **One Completion Gate**: After committing the intended work, run `./scripts/land` from the task worktree. It rebases onto current local `main`, runs the full suite against the exact commit, serializes the final fast-forward, updates the visible `main` checkout, and reconciles the running Docker MCP when runtime files changed.
- **Definition of Done**: Passing tests, committing, or pushing a task branch is not completion. Do not tell the user an implementation is complete until the command prints `LANDED TO MAIN: <sha>`. If it cannot land, report `NOT LANDED: <specific blocker>` and keep working when the blocker is agent-resolvable.
- **Concurrent Landings**: Do not bypass the landing command with a manual merge. If another agent advances `main` while tests run, `scripts/land` rebases and retests before trying again.
- **Protect Open IDEs**: Never stash, reset, clean, delete, or remove another worktree. A dirty tracked `main` is a blocker to report, not something to overwrite. Leave the task worktree and branch present after landing because an open IDE may still be using them.
- **Local Main First**: The user-facing product is the checked-out local `main` folder. Fetching, pushing, pull requests, release publication, and remote-mirror synchronization are separate tasks and are not part of the landing gate unless the user explicitly requests them.
- **Status Check**: Run `./scripts/land-status` when starting an implementation or diagnosing drift. It shows the visible main commit, open worktrees, branches ahead of main, and shared runtime readiness.
- **Workspace Memory**: For implementation, debugging, architecture, and review work, use `.agents/skills/unigrok-workspace-memory/SKILL.md` when available. Recall with the agent worktree's own full HEAD. After `scripts/land` succeeds, record one concise landed outcome; never write Git Notes directly. Memory mirror failure is reportable but does not undo a verified landing.
