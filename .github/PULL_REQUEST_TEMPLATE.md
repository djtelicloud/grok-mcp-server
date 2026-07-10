## Summary

<!-- What does this PR change and why? Link the issue it addresses, e.g. "Closes #1". -->

## Changes

<!-- Bullet list of the concrete changes. -->

## Fact-check checklist (required for docs & config changes)

Commands and names in this repo are frequently guessed wrong — please verify
each claim you make against the actual source:

- [ ] Endpoints match the code (health is **`/healthz`**, MCP is **`/mcp`**, UI is **`/ui/`** — see `src/http_server.py`)
- [ ] The Docker Compose service is named **`grok-mcp`** (see `docker-compose.yml`)
- [ ] CLI-plane readiness is probed with **`grok --check`** (see `src/utils.py`)
- [ ] Any environment variable you reference actually exists (`git grep <VAR_NAME>`)
- [ ] No destructive commands recommended without a warning (e.g. `docker compose down -v`, `rm -rf`, `git reset --hard`)

## Testing

- [ ] `uv run pytest -q` passes locally (631+ tests, ~8s)
- [ ] For docs-only changes: every command in the diff was actually run against a live checkout

## Security

- [ ] No secrets, tokens, or machine-specific absolute paths in the diff
