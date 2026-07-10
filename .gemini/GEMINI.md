# Grok-MCP Project-Specific Guidelines

Welcome, Gemini Agent! You are operating inside the local environment of the **Grok-MCP** codebase. Follow these project-specific directives on every execution:

## 1. Database Operations & Concurrency
* **Async-Native Drive**: Always interact with `GrokSessionStore` via the `aiosqlite` async connection.
* **Lock Protection**: Use the asynchronous lock (`asyncio.Lock`) for database writes to prevent database locking issues.
* **Checkpoint Control**: Ensure WAL (Write-Ahead Logging) checkpoints are cleaned up properly during shutdowns.

## 2. Telemetry and Secrets Safety
* **Telemetry Sanitization**: Never write raw `XAI_API_KEY`s or authorization bearer tokens to logs or telemetry rows. Sanitize them using regex patterns before persisting them to the database.
* **CLI Command Validation**: Ensure inputs processed by command lines are escaped to prevent command injection.
* **Secret Scan Validation**: Before saving any edits or committing configuration files, scan the changes to ensure no sensitive variables (such as `XAI_API_KEY`, `sk-` live Stripe keys, or `ghp-` GitHub PATs) are hardcoded.

## 3. Grok MCP Tool Routing
Select modular tools based on the nature of the request:
* **Server Health / Models**: Call `grok_mcp_status` or `list_models` for connectivity, circuit breaker, or routing status.
* **Project Context**: Call `list_project_files` or `read_local_file` to read local workspace context.
* **Git Operations**: Call `git_status`, `git_diff`, `git_log`, or `git_show` for read-only Git repository state.
* **Core Agent Tasks**: Call `agent` (using `mode=reasoning` or `mode=thinking`) for multi-step tasks.
* **Long-running Jobs**: Call `submit_research_job` (which uses `chat.defer()` server-side) for deferred research tasks.

## 4. Multi-Agent Git Protocol
* **No Main Branch Switch**: Do not switch the shared main folder branch. 
* **Isolated Worktrees**: Work on task-specific sibling worktrees (`gemini/task-name`).
* **Mandatory Landing Gate**: Commit the intended work and run `./scripts/land`. Do not manually merge and do not call an implementation complete until it prints `LANDED TO MAIN: <sha>`.
* **Open IDE Safety**: Never remove a worktree after landing or overwrite a dirty tracked `main`; another IDE may still be using it.

## 5. Verification Requirements
* `scripts/land` runs the full pytest suite against the exact commit that it fast-forwards to visible local `main`. Remote publishing is separate and only user-requested.
