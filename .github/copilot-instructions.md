# GitHub Copilot instructions for UniGrok

Read `AGENTS.md` and `.agents/AGENTS.md` before changing this repository. Their
shared rules are authoritative.

Use the shared workspace skill at `.github/skills/using-unigrok/SKILL.md` when
the user asks for Grok usage, "@grok" queries, second-model review, or
cross-repo UniGrok guidance in VS Code.

For normal feature work:

1. Keep the shared checkout on `main`; work in an isolated agent-prefixed
   worktree and branch such as `copilot/task-name` or `codex/task-name`.
2. Implement and run the relevant local tests. Commit with the accountable
   human sponsor and an accurate `Agent-Assisted-By:` trailer.
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
