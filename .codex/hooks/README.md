# Codex Hook Notes

These hooks are documentation-only. They do not claim that Codex currently
executes project-local hook files from `.codex/`.

## Before Thread Create

- Use `list_projects` first.
- Prefer a worktree environment unless the user explicitly asks for local.
- Use the matching template from `.codex/threads/templates/`.
- Emit `::created-thread` only after `create_thread` succeeds.

## Before Automation

- Use `automation_update`, not raw automation directives.
- Prefer `suggested_create` or `suggested_update` for worktree automations.
- Keep automations read-only unless the user explicitly approves mutation.
- Do not show raw RRULE strings to the user unless explicitly asked.

## Before Final Directive

- Emit Codex directives only for completed actions.
- Keep directive attributes single-line.
- Do not emit example directives in normal prose.

