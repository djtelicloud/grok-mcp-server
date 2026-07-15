# Workspace Rules

## Session rehydrate (new chats)

- **Start inside this product checkout** (or an agent worktree), not `$HOME`.
  Home sessions miss these rules and feel “dumber.”
- On first message after IDE reset, or when the user says **rehydrate** /
  **boot** / **where were we**, follow
  [`.agents/skills/session-rehydrate/SKILL.md`](skills/session-rehydrate/SKILL.md).
- Continuity lives in git/disk: private
  `../unigrok-intelligence/codex/continuity/active-work-latest.md` (if present),
  open PR notes, and `./scripts/land-status` — not chat memory.

## Communication discipline

- **Silent process, loud finish:** use tools and plan without narrating every
  step. Deliver one concise end-state answer (tables, decisions, links, blockers).
- Do not stream progress essays (“now I’ll check…”) unless the user asked for
  a live play-by-play.
- Prefer UniGrok MCP CLI/`fast` for cheap index-diff hive emits; keep visible
  output tiny. Insider silent-think doctrine is private (not public default).

## Human language (user-facing — mandatory)

The sponsor is **not** a git operator. Match **intent**, not literal VCS vocabulary.
Dyslexia-hostile jargon in answers is a product failure.

### Intent map (user words → agent duty)

| User says (examples) | Means | Agent does silently | Tell the user |
| --- | --- | --- | --- |
| Done? / finished? / is it pushed? / submitted? | Is my task complete and with the supervisor? | Open/update **draft PR** for your lane; hand exact head to supervisor when ready | **Ready for supervisor** / **Not ready** (+ one plain blocker) |
| Live? / shipped? / on main? / in production? | Is it the real product now? | Check protected main / deploy only if you are supervisor | **Live** / **Not live** (+ one plain reason) |
| Who did this? | Which **brand/agent** | Trailers / evidence | Lead with **Grok / Codex / Claude / …** — never “you wrote it” when an agent did |
| Clean up / too many folders | Remove finished scratchpads | Own worktree remove; supervisor may prune orphans | **Cleaned** / **Left X (why)** |
| Fix CI / make it green | Repair your PR checks | Fix and re-verify | **Green** / **Still red** (+ one plain cause) |

### Forbidden in user-facing answers (unless user says “git” or “technical”)

Do **not** lecture about: branch, worktree, rebase, fast-forward, origin, land,
merge, force-push, SHA, trailers, remotes, cherry-pick. Internally use them;
externally use **Ready / Not ready / Live / Not live / Blocked / Who (brand)**.

### Brand identity

- Lead with **your provider brand** (Grok CLI, Codex, Claude, Gemini, Copilot,
  Cursor, …) for work you did.
- Human sponsor is **accountable owner**, not the default “author of the idea.”
- Cursor Cloud / remote GitHub workers: **no laptop secrets, no tunnel, no
  loopback UniGrok** unless the user asked for local gateway help.

## Grok MCP Integration Rules
- **Shared MCP Endpoint**: The host-facing shared service endpoint is `http://localhost:4765/mcp` (Streamable HTTP). Port `8080` is container-internal only. Start it with `docker compose up --build -d` from the primary checkout unless the user explicitly asks for stdio mode.
- **Per-Agent Identity**: Every IDE/agent config should send `X-Client-ID` with a stable value such as `codex`, `claude-code`, `vscode`, or `antigravity`. This attributes telemetry and keeps sessions separate.
- **Credentials Boundary**: The xAI API key belongs to the running server/container environment (`XAI_API_KEY`), not to each IDE client. Do not ask the user to paste the xAI key into IDE MCP configs. If `UNIGROK_API_KEYS` is configured, IDE clients additionally need `Authorization: Bearer <client-token>`.
- **Grok Mentions**: Whenever the user mentions "@grok", "grok", or explicitly asks to query Grok, call the shared UniGrok MCP `agent` tool when it is available rather than answering directly using your own model weights or context.
- **Code Peer Reviews**: Whenever the user asks to peer review code, audit architectural files, or perform quality checks in this repository, invoke the shared UniGrok MCP `agent` tool for Grok's direct feedback when the MCP service is available.
- **Operational Source of Truth**: For installation snippets and IDE-specific config, use `docs/ide-setup.md`. For browser-based manual testing, open `http://localhost:4765/ui/`.
- **Multi-Step Implementation Plans**: When asked for a multi-step Implementation Plan, obtain a UniGrok second opinion (using agent mode `thinking` or `reasoning`) and improve the plan before showing it. Only do this if the user explicitly asks for this habit; do not silently spend metered API credits without request.

## Multi-Agent Git Coordination
- **PR-First Contribution Record**: Every change to `origin/main` goes through a pull request. After local verification, an authorized IDE agent may push only its own agent-prefixed task branch and open or update a draft pull request. If that agent lacks GitHub credentials, it hands Codex the exact commit so an authorized Codex session can publish the same branch and draft PR. A commit-only handoff is pre-PR evidence, not a substitute for the PR.
- **Humans Accountable, Agents Traceable**: The sponsoring GitHub user remains the accountable contributor. Record material IDE/model work with the canonical `Agent-Assisted-By:` trailer and advisory review with `Agent-Reviewed-By:` as defined in [docs/agent-attribution.md](../docs/agent-attribution.md). Never invent `Co-authored-by` identities: use that trailer only for a real person's linked GitHub email or an exact bot identity in `.github/agent-identities.json`.
- **Codex Owns Final Integration**: The Codex/project-admin role is independent of interface: Codex Desktop, CLI, GitHub Copilot, or another authorized Codex session may perform it. Contributor agents may inspect, edit, test, commit, push their own agent-prefixed branch, and open or update its draft PR. They must not push shared `main`, run `scripts/land`, merge, rebase shared `main`, publish releases or deployments, or delete worktrees unless they are explicitly acting as the Codex/project-admin integration session.
- **Shared Main Checkout**: Primary product folder stays on integrated `main`. Agents do not thrash it for experiments.
- **Do Not Branch-Switch the Shared Folder**: Parallel work uses separate scratchpads so other IDEs keep a stable main folder.
- **Worktrees Are Disposable Scratchpads**: One task, one contained tree only — under `<repo>/.worktrees/<agent>/<task>/` or `/tmp/unigrok-<agent>-<task>/`. Never sibling clutter like `Documents/…/grok-<feature>/` next to the real repo.
- **Worktree lifecycle**: Start task → contained tree → draft PR for supervisor → when **Live**, abandoned, or **new task** → **same agent removes its own finished tree**, then create a fresh one. Never delete another agent’s live tree or primary main. Supervisor may prune safe orphans.
- **Contributor done (user language)**: **Ready for supervisor** = your draft PR is open with exact head + verification. Not “I ran push/merge myself.”
- **Supervisor done (user language)**: **Live** only after protected integration succeeds (`LANDED TO MAIN` internally). Else **Not live** / **Blocked** with one plain reason.
- **Protect main and peers**: Never overwrite dirty main. Never remove peers’ live scratchpads. Your own finished scratchpad **must** go when the task ends.
- **Cursor Cloud**: GitHub coding needs no laptop UniGrok env/tunnel. Optional Grok = hosted twin only when connected.
- **Status Check**: Prefer `./scripts/land-status` silently. Many leftover trees = hygiene debt; clean yours before starting more.
- **`.worktrees/` is local only** (gitignored scratch).
- **Workspace Memory**: For implementation, debugging, architecture, and review work, use `.agents/skills/unigrok-workspace-memory/SKILL.md` when available. Recall with the agent worktree's own full HEAD. After `scripts/land` succeeds, record one concise landed outcome; never write Git Notes directly. Memory mirror failure is reportable but does not undo a verified landing.
