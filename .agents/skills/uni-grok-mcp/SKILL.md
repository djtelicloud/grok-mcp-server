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
http://localhost:4765/mcp
```

Start or refresh the service from the primary checkout with:
```bash
docker compose up --build -d
curl --fail -s http://localhost:4765/healthz
```

After at least one Grok credential plane is configured, use `/readyz` as the
model-access gate.

For IDE setup, use [docs/ide-setup.md](../../../docs/ide-setup.md) as the source of truth. Each IDE should use the same endpoint and set a stable `X-Client-ID` header (`codex`, `claude-code`, `vscode`, `antigravity`, etc.) so sessions and telemetry stay separated.

Credential boundary:
- `XAI_API_KEY` is server-side only and is required for API-plane calls. An authenticated CLI plane can serve compatible requests without it. IDE clients should never be configured with the raw xAI key.
- If `UNIGROK_API_KEYS` is set on the server, IDE clients also need `Authorization: Bearer <client-token>`.
- If neither `XAI_API_KEY` nor an authenticated Grok CLI plane is available inside the runtime, MCP transport can still answer `tools/list` and the browser UI can load, but real Grok calls cannot produce model answers.

For manual browser testing, open:
```text
http://localhost:4765/ui/
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
To launch the HTTP gateway server (which binds to `127.0.0.1:4765` by default):
```bash
uv run python main.py --http
```

---

## 3. Specialized Tools & Resources

Capability names are surface-specific. Treat the connected server's live
`tools/list`, `resources/list`, and `prompts/list` responses as authoritative:

- **Stable HTTP (`:4765/mcp`)**: `agent`, read-only PR review, status,
  discovery, and the disabled-by-default restart helper. This service is
  workspace-neutral.
- **Contributor Forge HTTP (`:4766/mcp`)**: the stable surface plus
  repository-scoped workspace-memory and Swarm tools.
- **Trusted stdio**: the full source registry, including deferred research:
  - `submit_research_job`: Start a deferred research job in xAI's infrastructure.
  - `get_research_job`: Check the status/polling updates of a deferred job.
  - `list_research_jobs`: List recently submitted jobs.
  Trusted stdio also registers `grok://` resources and reusable prompts such
  as `research_topic` and `fix_and_test`. Their presence in source does not
  imply that stable HTTP exposes them.

---

## 4. Multi-Agent Git Coordination Rules

Multiple IDE agents can collaborate on this project concurrently. Follow these rules to prevent merge conflicts, branch switching, and **disk clutter**:
1. **Never Branch-Switch `main`**: Keep the repository's shared primary checkout on `main`.
2. **Worktrees Are Scratchpads**: One agent-prefixed branch + one **contained** worktree per task only. Preferred path: `<repo>/.worktrees/<agent>/<task-slug>/` or `/tmp/unigrok-<agent>-<task>/`. **Never** create sibling product clones under `Documents/agentixai/grok-*` next to the real repo.
3. **Lifecycle**: start → work in that tree only → push PR → when landed/accepted or a **new** task is assigned → **delete your own finished worktree** (`git worktree remove` + prune) → create a **new** tree for the next task. Do not hoard finished trees.
4. **Codex Owns Final Integration**: Contributor agents do not push shared `main`, land, merge, release, or deploy. They **must** remove **their own** finished worktrees. Codex/project-admin may also prune orphans.
5. **Mandatory Landing Gate**: Codex reviews the handoff and runs `./scripts/land`. Do not claim integrated completion until it prints `LANDED TO MAIN: <sha>`.
6. **Visible Main Is the Product**: Local `main` integration is mandatory for integrated product state.
7. **Protect Peers And Main**: Never remove **another** agent’s live worktree or overwrite a dirty tracked `main`. Your own finished scratchpad is not sacred—remove it.
8. **Cursor Cloud**: GitHub-only workers need no laptop UniGrok tunnel/env; optional Grok is hosted twin only.
9. **Commit-Anchored Memory**: Use the `unigrok-workspace-memory` skill with the agent worktree’s own full HEAD. Codex records a landed outcome; do not write Git Notes directly.
