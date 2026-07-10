---
name: uni-grok-mcp
description: Guidelines and instructions for working on the uni-grok-mcp (Grok MCP) codebase. Activate this skill whenever you need to develop, test, debug, or verify changes in the uni-grok-mcp repository.
---

# Uni-Grok-MCP Developer Skill

This skill provides guidelines and instructions for developers and AI agents working on the `uni-grok-mcp` codebase.

## 1. Codebase Architecture & Files

- **Main Entry Point**: [main.py](../../../main.py) is the startup script which resolves the `.env` path and boots the server (stdio by default, HTTP gateway if `--http` or environment variables are set).
- **Core Server & Routing**: [src/server.py](../../../src/server.py) and [src/http_server.py](../../../src/http_server.py) handle direct stdio and HTTP gateway transport layers.
- **Custom Tools**: Modular tools are implemented under [src/tools/](../../../src/tools/).
- **Utilities**: [src/utils.py](../../../src/utils.py) contains database schemas (sqlite session and jobs tables), adapter logic, context summarization/compaction, and model catalog resolving.
- **Jobs Manager**: [src/jobs.py](../../../src/jobs.py) implements the background task runner and status poll for deferred research jobs using the `chat.defer()` API.

---

## 2. Common Developer Workflows

### Shared Docker MCP Service for IDE Agents
The shared local MCP endpoint is:
```text
http://localhost:8080/mcp
```

Start or refresh the service from the primary checkout with:
```bash
docker compose up --build -d
curl -s http://localhost:8080/healthz
```

For IDE setup, use [docs/ide-setup.md](../../../docs/ide-setup.md) as the source of truth. Each IDE should use the same endpoint and set a stable `X-Client-ID` header (`codex`, `claude-code`, `vscode`, `antigravity`, etc.) so sessions and telemetry stay separated.

Credential boundary:
- `XAI_API_KEY` is server-side only. It must be available to the process/container running UniGrok; IDE clients should not be configured with the raw xAI key.
- If `UNIGROK_API_KEYS` is set on the server, IDE clients also need `Authorization: Bearer <client-token>`.
- If neither `XAI_API_KEY` nor an authenticated Grok CLI plane is available inside the runtime, MCP transport can still answer `tools/list` and the browser UI can load, but real Grok calls cannot produce model answers.

For manual browser testing, open:
```text
http://localhost:8080/ui/
```

The browser UI is a static MCP test bench. It should test `/mcp` through JSON-RPC (`tools/list`, `tools/call`, raw requests) rather than calling `/v1`, `/metrics`, storage internals, or root-code admin endpoints.

### Package Dependency Sync
Always use Astral `uv` for lightning-fast dependency management:
```bash
uv sync
```

### Run Tests
Verification tests must pass before completing any task. Run the pytest suite using:
```bash
uv run pytest
```

### Launch Local Server
To launch the server locally for stdio communication:
```bash
uv run python main.py
```
To launch the HTTP gateway server (which binds to `127.0.0.1:8080` by default):
```bash
uv run python main.py --http
```

---

## 3. Specialized Tools & Resources

When querying Grok or using this server, take advantage of the following custom capabilities:

- **Unified Agent (`agent` tool)**: The core entry point for complex tasks. It handles multi-step reasoning, tool routing, and returns structured metadata.
- **Deferred Research Jobs**:
  - `submit_research_job`: Start a deferred research job in xAI's infrastructure.
  - `get_research_job`: Check the status/polling updates of a deferred job.
  - `list_research_jobs`: List recently submitted jobs.
- **MCP Resources**: The server exposes system state as resources under the `grok://` scheme (e.g. `grok://models`, `grok://status`, `grok://sessions`, `grok://jobs/{id}`).
- **MCP Prompts**: Common workflows are exposed as prompts (e.g. `research_topic`, `fix_and_test`).

---

## 4. Multi-Agent Git Coordination Rules

Multiple IDE agents can collaborate on this project concurrently. Follow these rules to prevent merge conflicts and branch switching issues:
1. **Never Branch-Switch `main`**: Keep the repository's shared primary checkout on `main`.
2. **Worktrees for Parallel Tasks**: Always create a separate git worktree and work on an agent-prefixed branch (e.g., `gemini/task-name`, `claude/task-name`).
3. **Mandatory Landing Gate**: Commit the intended work, then run `./scripts/land` from the task worktree. Do not manually merge or claim completion until it prints `LANDED TO MAIN: <sha>`.
4. **Visible Main Is the Product**: Local `main` integration is mandatory. Remote fetch/push/publication is separate and happens only when the user explicitly asks for it.
5. **Protect Open IDEs**: Never remove task worktrees after landing or overwrite a dirty tracked `main`; another IDE may still be using them.
6. **Commit-Anchored Memory**: Use the `unigrok-workspace-memory` skill to recall evidence using the agent worktree's own full HEAD. After landing, record a concise outcome against the exact SHA printed by `scripts/land`; do not write Git Notes directly.
