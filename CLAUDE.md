# CLAUDE.md

Guidance for Claude Code working in **uni-grok-mcp**. Shared multi-agent rules
(git coordination, endpoint, credentials boundary) live in
[.agents/AGENTS.md](.agents/AGENTS.md) — this file adds Claude-Code-specific
context and does not duplicate them. When the two conflict, AGENTS.md wins on
shared conventions.

## What this project is

A local-first, universal **MCP gateway for xAI's Grok models**. One server runs
on the machine, holds the xAI credential server-side, and lets every MCP client
(Claude Code, Claude Desktop, VS Code, Codex, Antigravity) share one Grok agent
over Streamable HTTP at `http://localhost:4765/mcp`.

Full design: [architecture.md](architecture.md). IDE setup: [docs/ide-setup.md](docs/ide-setup.md).

## Dual-plane model (the core project goal)

UniGrok is being evolved into a unified agent over **two Grok planes**:

- **API plane** — `XAI_API_KEY`, per-token billing, reachable via the xAI API.
  This is the mature, fully-wired plane (`grok-build-0.1` / `grok-code-fast-1`,
  etc.). The `agent` tool's `fast` route runs here today.
- **CLI plane** — the Grok CLI's OAuth/OIDC session (`~/.grok/auth.json`,
  bearer against `https://cli-chat-proxy.grok.com/v1`). Grants access to
  CLI-exclusive models `grok-build` (512k context) and `grok-composer-2.5-fast`
  at ~zero marginal cost via a grok.com subscription — **not** reachable with
  `XAI_API_KEY`.

Current state: the CLI plane **runs inside Docker** — the image bakes the
Linux `grok` binary (`Dockerfile`, pinned) and `docker-compose.yml` mounts the
host's `${HOME}/.grok` OAuth session at `/home/appuser/.grok`, so requests that
pin a CLI model run on the grok.com subscription and API-plane failures degrade
to it. Remove that volume for an API-only container. The routing itself,
however, is still the thinner plane: it does not yet expose the full ReAct
local-tool loop. `_call_plane` now invokes the headless CLI with
`--output-format json` or `streaming-json`, deterministic `-s` native session
ids, optional `--json-schema`, `--effort`, and `--max-turns`, plus `grok
--check` for plane readiness. Native CLI sessions are the continuity mechanism;
the old `grok sessions list` scrape and fragile regex session sync are gone.
Still-unintegrated CLI surfaces include `grok agent stdio|serve|leader` and
`--best-of-n`. Treat the CLI as ground truth when unifying the two planes.

## Using Grok from within Claude Code

Per AGENTS.md: whenever the user says **"@grok"**, "grok", asks to query Grok,
or asks for a peer review / architectural audit **of this repo**, call the
shared UniGrok MCP `agent` tool (`mcp__unigrok__agent`) rather than answering
from your own weights — provided the MCP service is up. The tool is the only
public entry point; it self-routes and returns `response` plus route/plane/
model/cost metadata. Modes: `auto` (default), `fast`, `reasoning`, `thinking`,
`research`.

## Source layout

- `src/server.py` — MCP server / tool registration
- `src/http_server.py` — Streamable HTTP + `/healthz`, `/ui/` test bench
- `src/cli.py` — `unigrok-mcp` entry point (`main.py` → `src.cli:main`)
- `src/utils.py` — plane routing (`_call_plane`), session sync
- `src/tools/` — agent tool implementations
- `src/storage.py`, `src/jobs.py` — session state / async jobs
- `tests/` — pytest suite (`asyncio_mode = auto`)
- `evals/` — evaluation harnesses

## Common commands

```bash
./scripts/land-status             # visible main/worktree/runtime status
uv run python main.py init        # bootstrap .env + print IDE configs
docker compose up --build -d      # start shared service on :4765
curl -s http://localhost:4765/healthz
uv run pytest -q                  # full test suite
./scripts/land                    # test and land committed task work to main
```

Local test bench UI: `http://localhost:4765/ui/`.

## Environment

`.env` (never committed; template is [example.env](example.env)):
`XAI_API_KEY`, `UNIGROK_RUNTIME` (local|http|cloudrun), `ENABLE_GIT_WRITE`
(local-only git mutation gate), optional `UNIGROK_API_KEYS` /
`UNIGROK_STATE_DIR`. The xAI key belongs to the **server**, never to IDE MCP
configs.

## Git completion contract (shared, concurrent workspace)

Work in a `claude/*` task worktree and leave the shared checkout on `main`.
After committing the intended changes, run `./scripts/land`. Passing tests or
committing a task branch is not completion: do not tell the user an
implementation is complete until the command prints `LANDED TO MAIN: <sha>`.
Never manually merge, overwrite a dirty tracked `main`, or remove the task
worktree after landing because another open IDE may still use it. Fetch, push,
PR, and release publication are separate operations performed only when the
user explicitly requests them.

For implementation, debugging, architecture, or review, use
`.agents/skills/unigrok-workspace-memory/SKILL.md`. Recall against the Claude
worktree's own full HEAD; after landing, record one concise verified outcome
against the exact SHA printed by `scripts/land`.
