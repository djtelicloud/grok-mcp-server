# Codex App Directives

Use these only after the matching Codex-visible action succeeds. Do not emit
them as examples in normal final answers.

## Thread

- `::created-thread{threadId="..."}`
- `::created-thread{pendingWorktreeId="..."}`

Emit after `codex_app.create_thread` succeeds.

## Git

- `::git-stage{cwd="/absolute/path"}`
- `::git-commit{cwd="/absolute/path"}`
- `::git-create-branch{cwd="/absolute/path" branch="codex/task-name"}`
- `::git-push{cwd="/absolute/path" branch="codex/task-name"}`
- `::git-create-pr{cwd="/absolute/path" branch="codex/task-name" url="https://..." isDraft=true}`

Emit only after the action actually succeeds. Keep attributes single-line.

## Inline Code Comments

Use `::code-comment{...}` only for actionable inline review feedback. Keep the
line range tight and use absolute file paths.

Required attributes:

- `title`
- `body`
- `file`

Optional attributes:

- `start`
- `end`
- `priority`

