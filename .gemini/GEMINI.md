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
* **Credential References Only**: Never copy `~/.gemini/config/config.json`, Google ADC, Grok OAuth, Claude auth, Codex auth, or a repository `.env` into this checkout. In particular, `.gemini/config.json` is tracked public project configuration and must never be replaced with the similarly named host file. Campaigns use the external owner-only provider profile, standard ADC discovery, and the loopback UniGrok MCP.

## 3. Grok MCP Tool Routing
Select modular tools based on the nature of the request:
* **Server Health / Models**: Call `grok_mcp_status` or `list_models` for connectivity, circuit breaker, or routing status.
* **Project Context**: The stable UniGrok service is workspace-neutral. Call
  `list_project_files` or `read_local_file` only when those tools are actually
  exposed in the current trusted contributor/stdio session. Otherwise provide
  deliberately selected excerpts through `agent.workspace_context`; never
  invent or imply access to the caller's files.
* **Git Operations**: Call `git_status`, `git_diff`, `git_log`, or `git_show` for read-only Git repository state.
* **Core Agent Tasks**: Call `agent` (using `mode=reasoning` or `mode=thinking`) for multi-step tasks.
* **Long-running Jobs**: Call `submit_research_job` (which uses `chat.defer()` server-side) for deferred research tasks.

## 4. Multi-Agent Git Protocol
* **No Main Branch Switch**: Do not switch the shared main folder branch. 
* **Isolated Worktrees**: Prefer Antigravity/provider home worktrees or `<repo>/.worktrees/gemini/<task>/` — never clutter Documents. Branch prefix `gemini/task-name`.
* **Pull Requests Are Canonical**: Every contribution reaches `origin/main` through a pull request. After local verification, push only your own `gemini/*` task branch and open or update a draft pull request when GitHub credentials are available. Otherwise give an authorized Codex session the exact commit for publication.
* **Codex Integration Owner**: The Codex/project-admin role is interface-independent; Codex Desktop, CLI, GitHub Copilot, or another authorized Codex session may perform final landing, protected-main mutation, approval, merge, tag, release, deployment, and synchronization.
* **Submit, Then Hand Off**: Commit the intended work, run relevant tests, publish the task branch and draft PR when possible, and provide the full commit SHA, changed paths, test results, known risks, human sponsor, and the canonical Google Gemini provider/model trailer from [docs/agent-attribution.md](../docs/agent-attribution.md). Do not run `scripts/land`, push shared `main`, merge/rebase shared `main`, or publish releases/deployments unless explicitly acting as the Codex/project-admin integration session. Exception: remove only your own finished disposable scratchpad; never delete peers' live trees or the primary main checkout.
* **Scratchpad cleanup**: After Live, abandonment, or a new task, remove **your own** finished worktree. Never delete peers' live trees or dirty main.
* **Sponsor status**: Use Ready / Not ready / Live / Not live / Blocked and lead with the agent brand. Keep Git terminology internal unless the sponsor asks for it.

## 5. Verification Requirements
* A Codex/project-admin session reviews the draft PR's exact current head and alone runs `scripts/land` from a `codex/*` integration branch before protected merge and local synchronization.
* Use `.agents/skills/unigrok-workspace-memory/SKILL.md` for commit-anchored recall. Pass the Gemini worktree's own full HEAD; Codex records the verified outcome after landing.
