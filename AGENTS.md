# AGENTS.md

Guidance for Codex working in **uni-grok-mcp**. Shared multi-agent rules
(git coordination, endpoint, credentials boundary) live in
[.agents/AGENTS.md](.agents/AGENTS.md) — this file adds Codex-specific
context and does not duplicate them. When the two conflict, AGENTS.md wins on
shared conventions.

## What this project is

A local-first, universal **MCP gateway for xAI's Grok models**. One server runs
on the machine, holds the xAI credential server-side, and lets every MCP client
(Codex, Codex Desktop, VS Code, Antigravity) share one Grok agent over
Streamable HTTP at `http://localhost:8080/mcp`.

Full design: [architecture.md](architecture.md). IDE setup:
[docs/ide-setup.md](docs/ide-setup.md).

## Dual-plane model (the core project goal)

- **API plane** — `XAI_API_KEY`, per-token billing, reachable via the xAI API.
  This is the mature, fully-wired plane. The `agent` tool's `fast` route runs
  here today.
- **CLI plane** — the Grok CLI OAuth/OIDC session in `~/.grok/auth.json`.
  Docker bakes the CLI binary and mounts the host authentication state. Native
  CLI sessions provide continuity; treat the CLI itself as ground truth when
  extending this plane.

## Using Grok from within Codex

Whenever the user says **"@grok"**, asks to query Grok, or requests a peer
review or architectural audit of this repo, call the shared UniGrok MCP
`agent` tool when it is available. The tool is the public entry point and
returns response plus route, plane, model, and cost metadata.

## Source layout

- `src/server.py` — MCP server and tool registration
- `src/http_server.py` — Streamable HTTP plus health and Control Center routes
- `src/cli.py` — `unigrok-mcp` entry point
- `src/utils.py` — plane routing, sessions, context, and model resolution
- `src/tools/` — modular agent tools
- `src/storage.py`, `src/jobs.py` — session state and deferred jobs
- `tests/` — pytest suite
- `evals/` — evaluation harnesses

## Common commands

```bash
./scripts/land-status             # visible main/worktree/runtime status
uv run python main.py init        # bootstrap .env and print IDE configs
docker compose up --build -d      # start shared service on :8080
curl -s http://localhost:8080/healthz
uv run pytest -q                  # full test suite
./scripts/land                    # test and land committed task work to main
```

Local Control Center: `http://localhost:8080/ui/`.

## Environment

`.env` is never committed. The server owns `XAI_API_KEY`; never copy the raw
key into IDE MCP configuration. See [example.env](example.env).

## Git completion contract

Codex and other IDE agents may operate concurrently. Work in a `codex/*` task
worktree and leave the shared checkout on `main`. After committing intended
changes, run `./scripts/land`. Passing tests or committing a task branch is not
completion: do not tell the user an implementation is complete until the
command prints `LANDED TO MAIN: <sha>`. Never remove the task worktree after
landing, because another open IDE may still use it. Fetch, push, PR, and release
publication are separate operations performed only when explicitly requested.
