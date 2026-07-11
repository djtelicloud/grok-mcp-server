# Contributing to UniGrok MCP

Thanks for helping improve UniGrok MCP. The project favors small, verified
changes that keep the shared Grok MCP gateway reliable across IDEs.

## Local Setup

```bash
uv sync
uv run python main.py init
```

Set `XAI_API_KEY` in `.env` for live API-plane calls, or authenticate the
container CLI plane with `docker compose run --rm grok-cli-auth` for compatible
subscription work. Tests should not require either real credential unless they
are explicitly live tests.

## Development Commands

```bash
uv run pytest
uv run python -m compileall -q src evals main.py
docker compose config
```

For the shared local service:

```bash
docker compose up --build -d
curl -s http://localhost:4765/healthz
```

## Pull Request Guidelines

- Keep changes scoped to one behavior or feature.
- Add or update tests for new tool behavior, CLI behavior, and runtime fixes.
- Keep credentials out of commits. Use `.env`; never commit real API keys.
- Prefer existing helpers and architecture over new parallel abstractions.
- Update `README.md`, `architecture.md`, or `docs/ide-setup.md` when behavior
  changes user setup, transport, tools, or runtime expectations.
- Run `uv run pytest` before opening a PR.

Security vulnerabilities should be reported privately as described in
[SECURITY.md](SECURITY.md), not opened as public issues.

## Multi-Agent Coordination

This repository is often used from several IDE agents at once. Keep the shared
checkout on integrated `main`, and do experimental work in an agent-prefixed
worktree branch such as `codex/my-change`, `claude/my-change`, or
`gemini/my-change`.
