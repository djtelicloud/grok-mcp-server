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
- **Brand next steps are mandatory after the status table.** Every brand must
  know its own strengths and offer **1–2 plain-title next smartest tasks** it
  can actually do (problem or benefit), not a table-only “ready.” Details and
  the brand map live in the session-rehydrate skill §6b.

## Communication discipline

**Silent human radio:** Chat pollution is a product bug. Keep reasoning, diffs,
tool payloads, full logs, and progress essays off-chat unless the user asks for
technical detail. At a finish or meaningful state change, emit one short
**brand + status + plain task title** line; a link or (#N) may follow.
Long or continuously monitored work uses that format only for real state
changes or user-requested live updates. Required boot summaries, safety prompts,
and blocking questions are explicit exceptions.
- Prefer UniGrok MCP CLI/`fast` for hard judgment; keep visible output tiny.
  Insider silent-think doctrine is private (not public default).
- **Public intelligence packs:** distilled gym wins for clones live under
  `docs/public-intelligence/`. After Live work, ask once: promote a scrubbed
  pack/skill? Never auto-sync private intelligence or raw memory to public.

## Human language (user-facing — mandatory)

The sponsor is **not** a git operator. Match **intent**, not literal VCS vocabulary.
Dyslexia-hostile jargon in answers is a product failure.

### Intent map (user words → agent duty)

| User says (examples) | Means | Agent does silently | Tell the user |
| --- | --- | --- | --- |
| Done? / finished? / is it pushed? / submitted? | Is my task complete and with the supervisor? | Open/update **draft PR** for your lane; hand exact HEAD to supervisor when ready | **Ready for supervisor** / **Not ready** (+ one plain blocker) |
| Live? / shipped? / on main? / in production? | Is it the real product now? | Check protected main / deploy only if you are supervisor | **Live** / **Not live** (+ one plain reason) |
| Who did this? | Which **brand/agent** | Trailers / evidence | Lead with **Grok / Codex / Claude / …** — never “you wrote it” when an agent did |
| Clean up / too many folders | Remove finished scratchpads | Own worktree remove; supervisor may prune orphans | **Cleaned** / **Left X (why)** |
| Fix CI / make it green | Repair your PR checks | Fix and re-verify | **Green** / **Still red** (+ one plain cause) |

### Task titles, not ticket numbers

A draft request to the coordinator **is** a finished (or ready) **task packet**.
The **task title** is what humans hear. Internal ticket numbers are optional
footnotes for links — never the lead.

**Template (every agent):**

```text
[Brand]: [Live | Ready for supervisor | Not ready | Blocked] — [plain English task title].
[Optional one outcome line.]
[Optional link; number only as (#N) at the end if needed.]
```

**Bad:** “Promoting PR #164. Yours is #165.”

**Good:** “Codex: Live — refreshed maintainer handoff.”

**Good:** “Grok: Ready for supervisor — public intelligence pack v0.”

Write machine titles so the **human half is readable** (e.g. “public intelligence
pack v0”), not only `feat(docs): …` jargon when speaking to the sponsor.

### Forbidden in user-facing answers (unless user says “git” or “technical”)

Do **not** lecture about: branch, worktree, rebase, fast-forward, origin, land,
merge, force-push, SHA, trailers, remotes, or cherry-pick. Do **not** lead with
PR numbers. Internally use them; externally use **Ready / Not ready / Live /
Not live / Blocked / Who (brand)** plus the **task title**.

### Brand identity

- Lead with **your provider brand** (Grok CLI, Codex, Claude, Gemini, Copilot,
  Cursor, …) for work you did.
- Human sponsor is **accountable owner**, not the default “author of the idea.”
- Cursor Cloud / remote GitHub workers: **no laptop secrets, no tunnel, no
  loopback UniGrok** unless the user asked for local gateway help.

## Grok MCP Integration Rules
- **Shared MCP Endpoint**: The host-facing shared service endpoint is `http://localhost:4765/mcp` (Streamable HTTP). Port `8080` is container-internal only. Start it with `docker compose up --build -d` from the primary checkout unless the user explicitly asks for stdio mode.
- **Per-Agent Identity**: Every IDE/agent config should send `X-Client-ID` with a stable value such as `cursor`, `cursor-forge`, `codex`, `claude-code`, `vscode`, `vscode-forge`, or `antigravity`. This attributes telemetry and keeps sessions separate. Cursor uses `~/.cursor/mcp.json` or project `.cursor/mcp.json` (`cursor` / `cursor-forge`); repo-root `.mcp.json` (`vscode` / `vscode-forge`) is the VS Code path — do not copy those labels into Cursor sessions.
- **Credentials Boundary**: The xAI API key belongs to the running server/container environment (`XAI_API_KEY`), not to each IDE client. Do not ask the user to paste the xAI key into IDE MCP configs. If `UNIGROK_API_KEYS` is configured, IDE clients additionally need `Authorization: Bearer <client-token>`.
- **Grok Mentions**: Whenever the user mentions "@grok", "grok", or explicitly asks to query Grok, call the shared UniGrok MCP `agent` tool when it is available rather than answering directly using your own model weights or context.
- **Code Peer Reviews**: Whenever the user asks to peer review code, audit architectural files, or perform quality checks in this repository, invoke the shared UniGrok MCP `agent` tool for Grok's direct feedback when the MCP service is available.
- **Operational Source of Truth**: For installation snippets and IDE-specific config, use `docs/ide-setup.md`. For browser-based manual testing, open `http://localhost:4765/ui/`.
- **Multi-Step Implementation Plans**: When asked for a multi-step Implementation Plan, obtain a UniGrok second opinion (using agent mode `thinking` or `reasoning`) and improve the plan before showing it. Only do this if the user explicitly asks for this habit; do not silently spend metered API credits without request.

## Multi-Agent Git Coordination
- **PR-First Contribution Record**: Every change to `origin/main` goes through a pull request. After local verification, an authorized IDE agent may push only its own agent-prefixed task branch and open or update a draft pull request. If that agent lacks GitHub credentials, it hands Codex the exact commit so an authorized Codex session can publish the same branch and draft PR. A commit-only handoff is pre-PR evidence, not a substitute for the PR.
- **Humans Accountable, Agents Traceable**: The sponsoring GitHub user remains the accountable contributor. Record material IDE/model work with the canonical `Agent-Assisted-By:` trailer and advisory review with `Agent-Reviewed-By:` as defined in [docs/agent-attribution.md](../docs/agent-attribution.md). Never invent `Co-authored-by` identities: use that trailer only for a real person's linked GitHub email or an exact bot identity in `.github/agent-identities.json`.
- **Codex Owns Final Integration**: The Codex/project-admin role is independent of interface: Codex Desktop, CLI, GitHub Copilot, or another authorized Codex session may perform it. Contributor agents may inspect, edit, test, commit, push their own agent-prefixed branch, and open or update its draft PR. They must not push shared `main`, run `scripts/land`, merge, rebase shared `main`, or publish releases or deployments unless they are explicitly acting as the Codex/project-admin integration session. Exception: a contributor **may remove only its own finished disposable scratchpad** (see Worktree lifecycle); never delete peers’ live trees or the primary main checkout.
- **Shared Main Checkout**: Primary product folder stays on integrated `main`. Agents do not thrash it for experiments.
- **Do Not Branch-Switch the Shared Folder**: Parallel work uses separate scratchpads so other IDEs keep a stable main folder.
- **Worktrees Are Disposable Scratchpads**: One task, one contained tree only — under `<repo>/.worktrees/<agent>/<task>/` or `/tmp/unigrok-<agent>-<task>/`. Never sibling clutter like `Documents/…/grok-<feature>/` next to the real repo.
- **Worktree lifecycle**: Start task → contained tree → draft PR for supervisor → when **Live**, abandoned, or **new task** → **same agent removes its own finished tree**, then create a fresh one. Never delete another agent’s live tree or the primary main checkout. Supervisor may prune safe orphans.
- **Contributor done (user language)**: **Ready for supervisor** = your draft PR is open with exact HEAD + verification. Not “I ran push/merge myself.”
- **Supervisor done (user language)**: **Live** only after protected integration succeeds (`LANDED TO MAIN` internally). Else **Not live** / **Blocked** with one plain reason.
- **Protect main and peers**: Never overwrite dirty main. Never remove peers’ live scratchpads. Your own finished scratchpad **must** go when the task ends.
- **Cursor Cloud**: GitHub coding needs no laptop UniGrok env/tunnel. Optional Grok = hosted twin only when connected.
- **Status Check**: Prefer `./scripts/land-status` silently. Many leftover trees = hygiene debt; clean yours before starting more.
- **`.worktrees/` is local only** (gitignored scratch).
- **Workspace Memory**: For implementation, debugging, architecture, and review work, use `.agents/skills/unigrok-workspace-memory/SKILL.md` when available. Recall with the agent worktree's own full HEAD. After `scripts/land` succeeds, record one concise landed outcome; never write Git Notes directly. Memory mirror failure is reportable but does not undo a verified landing.

## Cursor Automations (PR Approver / Security Reviewer / Bugbot)

Cursor-native always-on mirror for agents that load `.cursor/rules`:
[`.cursor/rules/cursor-automations-single-pass.mdc`](../.cursor/rules/cursor-automations-single-pass.mdc).
Keep that file’s automation-role bullets aligned with this section. Interactive
Composer is not bound by the automation paths below; the mirror is for
Automations / Bugbot roles only.

These rules apply to Cursor Automations and Bugbot Autofix on this repo:

- **Single-agent only.** Do not spawn parallel subagents, “review modules,” or repeated fan-out batches. One serial pass per run.
- **No branch thrash.** Stay on the PR head you were given. Do not create extra branches/PRs unless the automation’s job is explicitly Autofix and a fix commit is required.
- **One action per head SHA.** Post at most one approval **or** one concise review comment for a given PR head. If an active run for the same automation + head already exists, exit without duplicating work.
- **Ignore bot echo.** Do not re-trigger meaningful work solely because `cursor[bot]`, Bugbot, Copilot, Gemini, or Codex left a review comment.
- **Approver path:** wait for Cursor Bugbot to finish; require green required checks; approve only low-risk diffs with no unresolved medium/high Bugbot findings; otherwise comment blockers and stop.
- **Security Reviewer path:** inspect the PR diff + existing review threads once; report only actionable unresolved security findings; exit cleanly. Never attempt multi-module orchestration.
- **Bugbot Autofix path:** apply the minimal doc/code fix for the cited finding on the existing PR branch; commit and push that branch only.
