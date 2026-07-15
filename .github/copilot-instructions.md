# GitHub Copilot instructions for UniGrok

Read `AGENTS.md` and `.agents/AGENTS.md` before changing this repository. Their
shared rules are authoritative.

Use the shared workspace skill at `.github/skills/using-unigrok/SKILL.md` when
the user asks for Grok usage, "@grok" queries, second-model review, or
cross-repo UniGrok guidance in VS Code.

## Session rehydrate — brand next steps (Copilot / Kimi)

On **rehydrate** / **boot** / first message after IDE reset, follow
`.agents/skills/session-rehydrate/SKILL.md`. After the short status table you
**must** emit **Next smartest steps (Copilot)** (or Kimi if that is the host
model brand) with **1–2 concrete offers**. A table-only rehydrate is incomplete.

> [!IMPORTANT]
> **Glossary: Do not conflate "Hydrate" concepts**
> - **Session Rehydrate**: Booting intelligence from git/disk across chats.
> - **Process / Telemetry Hydration**: Recovering bounded in-memory runtime state from the configured durable store after restart (`src/hydration.py`).
> - **Hydration Lanes / Scratchpads**: Disposable Git worktrees used for contributor isolation.
> Never mix these up; they are three completely separate boundaries.

- **Your strengths:** VS Code in-editor speed; this file’s fidelity; VS Code MCP
  client setup; plain-title human radio for sponsor status.
- **Pick live work** from land-status, open Ready packets, or VS Code / Copilot
  setup gaps — never invent supervisor land authority.
- Lead with brand + Ready/Live/Blocked + plain task title. Never lead with PR
  numbers.

For normal feature work:

1. Keep the shared checkout on `main`; work in an isolated agent-prefixed
   worktree and branch such as `copilot/task-name` or `codex/task-name`.
2. Implement and run the relevant local tests. Commit with the accountable
   human sponsor and the canonical GitHub Copilot provider/model trailer from
   `docs/agent-attribution.md`.
3. Push only your own task branch and open or update a draft pull request. The
   PR must include the exact head SHA, changed paths, test results, known risks,
   generated files, sponsor, and agent provenance.
4. Never push shared `main`, rewrite a published branch after current-head
   review, merge, release, deploy, or delete another worktree as an ordinary
   contributor session.

The Codex/project-admin role is not tied to Codex Desktop. When the user asks
this Copilot/Codex session to finish integration and it has the required local
and GitHub access, it may act as project admin: review the exact PR head and
required checks, use a `codex/*` integration branch, run `./scripts/land`,
complete the protected GitHub merge, and synchronize local `main` with
`origin/main`. Opening a PR or passing tests alone is never integrated
completion.

## Cursor Automations role gate (VS Code Copilot is not an automation role)

The single-pass Automations law in `.agents/AGENTS.md` and its
`.cursor/rules/cursor-automations-single-pass.mdc` mirror apply **only** to
Cursor Automations and Bugbot Autofix roles (PR Approver, Security Reviewer,
Bugbot Autofix). VS Code Copilot / interactive Composer chat is not an
automation role — do not treat those rules as commit/push authorization here.
Keep their automation-role bullets aligned when editing either file, but do not
expand them into general Copilot instructions.
