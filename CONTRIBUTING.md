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
- Open contributions as pull requests; do not push directly to protected
  `main`.
- Record the exact head commit SHA in the handoff and refresh review evidence
  after every new commit. A review of an older head is stale.
- Add or update tests for new tool behavior, CLI behavior, and runtime fixes.
- Keep credentials out of commits. Use `.env`; never commit real API keys.
- Prefer existing helpers and architecture over new parallel abstractions.
- Update `README.md`, `architecture.md`, or `docs/ide-setup.md` when behavior
  changes user setup, transport, tools, or runtime expectations.
- Run `uv run pytest` before opening a PR.

Human contributors and coding agents use the same evidence contract: explain
the intent, list changed paths, report exact verification commands and results,
identify the accountable GitHub user and assisting IDE/model, and disclose known
risks or generated files. Use `Agent-Assisted-By:` for agent provenance; use
`Co-authored-by:` only for a real GitHub account whose linked email should receive
contribution credit. Grok review comments are advisory;
they do not authorize a merge. Codex reviews the current head and owns landing,
merge, tag, and release decisions. See
[ADR 0001](docs/adr/0001-cloud-control-plane-governance.md) for the current
local landing contract and the explicitly not-yet-live remote broker design.

Security vulnerabilities should be reported privately as described in
[SECURITY.md](SECURITY.md), not opened as public issues.

## Multi-Agent Coordination

This repository is often used from several IDE agents at once. Keep the shared
checkout on integrated `main`, and do experimental work in an agent-prefixed
worktree branch such as `codex/my-change`, `claude/my-change`, or
`gemini/my-change`.

An agent handoff must include its task branch or PR, full commit SHA, changed
paths, test evidence, unresolved risks, human sponsor, and agent provenance.
For a local IDE agent, Codex publishes the branch and draft PR; an outside human
contributor may publish their own fork/branch and PR. Agents other than Codex
must not run `scripts/land`, merge, push shared `main`, publish releases, delete
shared worktrees, or treat an advisory model review as approval.
