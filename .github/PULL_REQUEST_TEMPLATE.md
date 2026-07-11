## Summary

<!-- What does this PR change and why? Link the issue it addresses, e.g. "Closes #1". -->

## Changes

<!-- Bullet list of the concrete changes. -->

## Head and handoff evidence

<!--
Exact head SHA reviewed/tested:
Contributor or agent identity:
Changed paths:
Known risks, generated files, or follow-up work:
-->

## Fact-check checklist (required for docs & config changes)

Commands and names in this repo are frequently guessed wrong — please verify
each claim you make against the actual source:

- [ ] Endpoints match the code (health is **`/healthz`**, MCP is **`/mcp`**, UI is **`/ui/`** — see `src/http_server.py`)
- [ ] The Docker Compose service is named **`grok-mcp`** (see `docker-compose.yml`)
- [ ] CLI-plane readiness is probed with **`grok --check`** (see `src/utils.py`)
- [ ] Any environment variable you reference actually exists (`git grep <VAR_NAME>`)
- [ ] No destructive commands recommended without a warning (e.g. `docker compose down -v`, `rm -rf`, `git reset --hard`)

## Testing

- [ ] `uv run pytest -q` passes locally
- [ ] For docs-only changes: every command in the diff was actually run against a live checkout
- [ ] Results above apply to this PR's current head SHA (not an earlier commit)
- [ ] Any `@grok` review shown as current names this exact head SHA

## Security

- [ ] No secrets, tokens, or machine-specific absolute paths in the diff
- [ ] Authentication, workflow, deployment, or landing changes include a
      fail-closed test and identify the trust boundary they change
- [ ] Contributor-controlled text is treated as untrusted evidence, not as
      workflow commands or model-authorized mutation

## Codex/project-admin landing gate

<!-- Checked by Codex/project-admin automation, not the contributor or user. -->

- [ ] Required CI and CODEOWNER review pass on the current head
- [ ] Codex disposition applies to the current head
- [ ] Landing/merge receipt is captured by the currently operative integration path
