# Workspace Rules

## Grok MCP Integration Rules
- **Shared MCP Endpoint**: The local shared service endpoint is `http://localhost:8080/mcp` (Streamable HTTP). Start it with `docker compose up --build -d` from the primary checkout unless the user explicitly asks for stdio mode.
- **Per-Agent Identity**: Every IDE/agent config should send `X-Client-ID` with a stable value such as `codex`, `claude-code`, `vscode`, or `antigravity`. This attributes telemetry and keeps sessions separate.
- **Credentials Boundary**: The xAI API key belongs to the running server/container environment (`XAI_API_KEY`), not to each IDE client. Do not ask the user to paste the xAI key into IDE MCP configs. If `UNIGROK_API_KEYS` is configured, IDE clients additionally need `Authorization: Bearer <client-token>`.
- **Grok Mentions**: Whenever the user mentions "@grok", "grok", or explicitly asks to query Grok, call the shared UniGrok MCP `agent` tool when it is available rather than answering directly using your own model weights or context.
- **Code Peer Reviews**: Whenever the user asks to peer review code, audit architectural files, or perform quality checks in this repository, invoke the shared UniGrok MCP `agent` tool for Grok's direct feedback when the MCP service is available.
- **Operational Source of Truth**: For installation snippets and IDE-specific config, use `docs/ide-setup.md`. For browser-based manual testing, open `http://localhost:8080/ui/`.

## Multi-Agent Git Coordination
- **Shared Main Checkout**: This repository may be opened by several local IDE agents at once. The primary checkout should stay on `main` and represent the latest integrated local development state.
- **Do Not Branch-Switch the Shared Folder**: Agents must not switch branches or perform experimental edits in the shared `main` checkout when other IDEs may be using it. Branch switching changes the files visible to every agent using this folder.
- **Use Per-Agent Worktrees for Parallel Work**: For parallel work, create a sibling Git worktree per agent and work on an agent-prefixed branch, such as `codex/task-name`, `claude/task-name`, `gemini/task-name`, or `grok/task-name`.
- **Verify Before Integration**: Before integrating, ensure the worktree is clean except for intended changes, run relevant tests, and commit the work on the agent branch.
- **Fast-Forward Main**: Integrate by fast-forwarding local `main` when possible. Preferred flow: `git switch main`, then `git merge --ff-only <agent-branch>`.
- **Rebase on Conflict**: If fast-forward is not possible, rebase the agent branch onto `main`, rerun tests, then fast-forward `main`.
- **Keep Main Usable**: Do not leave `main` dirty. Do not merge failing, partial, or unverified work into `main`.
- **Goal**: Any IDE opening the main project folder should always see the full latest integrated code without needing branch awareness.
