# Workspace Rules

## Session rehydrate (new chats)

- **Start inside this product checkout** (or an agent worktree), not `$HOME`.
  Home sessions miss these rules and feel “dumber.”
- On first message after IDE reset, or when the user says **rehydrate** /
  **boot** / **where were we**, follow
  [`.agents/skills/session-rehydrate/SKILL.md`](skills/session-rehydrate/SKILL.md).
- Continuity lives in git/disk: private
  `unigrok-intelligence/codex/continuity/active-work-latest.md` (if present),
  open PR notes, and `./scripts/land-status` — not chat memory.

## Communication discipline

- **Silent process, loud finish:** use tools and plan without narrating every
  step. Deliver one concise end-state answer (tables, decisions, links, blockers).
- Do not stream progress essays (“now I’ll check…”) unless the user asked for
  a live play-by-play.
- Prefer UniGrok MCP CLI/`fast` for cheap index-diff hive emits; keep visible
  output tiny. Insider silent-think doctrine is private (not public default).

## Grok MCP Integration Rules
- **Shared MCP Endpoint**: The host-facing shared service endpoint is `http://localhost:4765/mcp` (Streamable HTTP). Port `8080` is container-internal only. Start it with `docker compose up --build -d` from the primary checkout unless the user explicitly asks for stdio mode.
- **Per-Agent Identity**: Every IDE/agent config should send `X-Client-ID` with a stable value such as `codex`, `claude-code`, `vscode`, or `antigravity`. This attributes telemetry and keeps sessions separate.
- **Credentials Boundary**: The xAI API key belongs to the running server/container environment (`XAI_API_KEY`), not to each IDE client. Do not ask the user to paste the xAI key into IDE MCP configs. If `UNIGROK_API_KEYS` is configured, IDE clients additionally need `Authorization: Bearer <client-token>`.
- **Grok Mentions**: Whenever the user mentions "@grok", "grok", or explicitly asks to query Grok, call the shared UniGrok MCP `agent` tool when it is available rather than answering directly using your own model weights or context.
- **Code Peer Reviews**: Whenever the user asks to peer review code, audit architectural files, or perform quality checks in this repository, invoke the shared UniGrok MCP `agent` tool for Grok's direct feedback when the MCP service is available.
- **Operational Source of Truth**: For installation snippets and IDE-specific config, use `docs/ide-setup.md`. For browser-based manual testing, open `http://localhost:4765/ui/`.

## Multi-Agent Git Coordination
- **PR-First Contribution Record**: Every change to `origin/main` goes through a pull request. After local verification, an authorized IDE agent may push only its own agent-prefixed task branch and open or update a draft pull request. If that agent lacks GitHub credentials, it hands Codex the exact commit so an authorized Codex session can publish the same branch and draft PR. A commit-only handoff is pre-PR evidence, not a substitute for the PR.
- **Humans Accountable, Agents Traceable**: The sponsoring GitHub user remains the accountable contributor. Record material IDE/model work with the canonical `Agent-Assisted-By:` trailer and advisory review with `Agent-Reviewed-By:` as defined in [docs/agent-attribution.md](../docs/agent-attribution.md). Never invent `Co-authored-by` identities: use that trailer only for a real person's linked GitHub email or an exact bot identity in `.github/agent-identities.json`.
- **Codex Owns Final Integration**: The Codex/project-admin role is independent of interface: Codex Desktop, CLI, GitHub Copilot, or another authorized Codex session may perform it. Contributor agents may inspect, edit, test, commit, push their own agent-prefixed branch, and open or update its draft PR. They must not push shared `main`, run `scripts/land`, merge, rebase shared `main`, publish releases or deployments, or delete worktrees unless they are explicitly acting as the Codex/project-admin integration session.
- **Shared Main Checkout**: This repository may be opened by several local IDE agents at once. The primary checkout should stay on `main` and represent the latest integrated local development state.
- **Do Not Branch-Switch the Shared Folder**: Agents must not switch branches or perform experimental edits in the shared `main` checkout when other IDEs may be using it. Branch switching changes the files visible to every agent using this folder.
- **Use Per-Agent Worktrees**: Every implementation runs in an agent-prefixed task worktree such as `codex/task-name`, `claude/task-name`, `gemini/task-name`, or `grok/task-name`. Never edit implementation files directly in the shared `main` checkout.
- **One Completion Gate**: The contributor publishes the draft PR when credentials permit. A Codex/project-admin session verifies its exact current head and required checks, then runs `./scripts/land` only from a `codex/*` integration branch. The command refuses contributor-prefixed branches, checks generated artifacts without amending commits, runs the full suite, serializes the local fast-forward, and reconciles the contributor runtime. That session then completes the protected GitHub merge and synchronizes local `main` to the resulting `origin/main`.
- **Definition of Done**: Passing tests, committing, or pushing a task branch is not completion. Do not tell the user an implementation is complete until the command prints `LANDED TO MAIN: <sha>`. If it cannot land, report `NOT LANDED: <specific blocker>` and keep working when the blocker is agent-resolvable.
- **Concurrent Landings**: Do not bypass the landing command with a manual merge. If the reviewed branch is behind `main`, or another agent advances `main` while tests run, `scripts/land` fails closed. Rebase the task branch, publish the new head, rerun verification, and obtain exact-head review before landing it.
- **Protect Open IDEs**: Never stash, reset, clean, delete, or remove another worktree. A dirty tracked `main` is a blocker to report, not something to overwrite. Leave the task worktree and branch present after landing because an open IDE may still be using them.
- **Protected Remote Is Canonical**: `origin/main` is the public contribution system of record. Local `main` must be synchronized after the protected PR merge and remains the user-facing local checkout. Contributor agents may fetch and publish only their own task branch and draft PR. The Codex/project-admin role exclusively owns protected-main mutation, final PR disposition, tags, releases, deployments, and remote-mirror decisions under the user's standing authorization.
- **Status Check**: Run `./scripts/land-status` when starting an implementation or diagnosing drift. It shows the visible main commit, open worktrees, branches ahead of main, and shared runtime readiness.
- **Workspace Memory**: For implementation, debugging, architecture, and review work, use `.agents/skills/unigrok-workspace-memory/SKILL.md` when available. Recall with the agent worktree's own full HEAD. After `scripts/land` succeeds, record one concise landed outcome; never write Git Notes directly. Memory mirror failure is reportable but does not undo a verified landing.
