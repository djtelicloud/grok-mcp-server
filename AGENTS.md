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
Streamable HTTP at `http://localhost:4765/mcp`.

Full design: [architecture.md](architecture.md). IDE setup:
[docs/ide-setup.md](docs/ide-setup.md).

## Dual-plane model (the core project goal)

UniGrok is being evolved into a unified agent over **two Grok planes**:

- **API plane** — `XAI_API_KEY`, per-token billing, reachable via the xAI API.
  Its live catalog includes the exact `grok-build-0.1` coding-model slug.
- **CLI plane** — the Grok CLI's OAuth/OIDC session (`~/.grok/auth.json`,
  bearer against `https://cli-chat-proxy.grok.com/v1`). Grants access to
  the models returned by the authenticated live `grok models` catalog through
  a grok.com subscription. Current observed IDs include `grok-4.5` and
  `grok-composer-2.5-fast`; never infer CLI availability from a product name.

The **Grok Build** coding-agent product and the API slug `grok-build-0.1` are
related but not interchangeable catalog identities. Plane membership is always
the exact intersection/difference of the two live provider catalogs.

Current state: the CLI plane **runs inside Docker** — the image bakes the
Linux `grok` binary (`Dockerfile`, pinned) and `docker-compose.yml` persists a
machine-level OAuth session in the dedicated `unigrok-cli-auth` volume. The
default `cli_first` policy prefers compatible, unpinned CLI work. Explicit
plane requests should use `fallback_policy=same_plane` when crossing credential
planes is forbidden; `cross_plane` permits bounded failover. The CLI execution
adapter does not expose the full API ReAct local-tool loop. `_call_plane`
invokes the headless CLI with
`--output-format json` or `streaming-json`, per-session `--session-id`
creation and `--resume` continuation (with `--fork-session` on collision — the
native id is stored per session, not a deterministic hash), optional
`--json-schema`, `--effort`, and `--max-turns`, plus a `grok models` probe for
plane readiness. Native CLI sessions are the continuity mechanism;
the old `grok sessions list` scrape and fragile regex session sync are gone.
Still-unintegrated CLI surfaces include `grok agent stdio|serve|leader` and
`--best-of-n`. Treat the CLI as ground truth when unifying the two planes.

## Using Grok from within Codex

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
uv run python main.py init        # bootstrap .env and print IDE configs
docker compose up --build -d      # start shared service on :4765
curl -s http://localhost:4765/healthz
uv run pytest -q                  # full test suite
./scripts/land                    # test and land committed task work to main
```

Local Control Center: `http://localhost:4765/ui/`.

## Environment

`.env` (never committed; template is [example.env](example.env)):
`XAI_API_KEY`, `UNIGROK_RUNTIME` (local|http|cloudrun), `ENABLE_GIT_WRITE`
(local-only git mutation gate), optional `UNIGROK_API_KEYS` /
`UNIGROK_STATE_DIR`. The xAI key belongs to the **server**, never to IDE MCP
configs.

## Git completion contract

Codex is the permanent final Git and release-integration owner for this
repository. That role is interface-independent: an authorized Codex Desktop,
CLI, GitHub Copilot, or other Codex session may act as project admin. Work in a
`codex/*` task or integration worktree and leave the shared checkout on `main`.
Every contribution reaches protected `origin/main` through a pull request.
After local verification, each authorized IDE agent may push only its own
agent-prefixed task branch and open or update a draft PR. If it lacks GitHub
credentials, it hands the exact commit to a Codex session for publication.
Every draft PR or handoff must name the full commit SHA, changed paths, tests,
risks, human sponsor, and canonical provider/model provenance from
[docs/agent-attribution.md](docs/agent-attribution.md). Material work uses
`Agent-Assisted-By:` and advisory review uses `Agent-Reviewed-By:`. A Codex/project-admin
session reviews the exact current head, binds approval to that head, alone runs
`./scripts/land` from a `codex/*` integration branch, completes the protected
merge, and synchronizes local `main`. Contributor agents must not push shared
`main`, land, merge, release, or deploy unless explicitly acting in that
integration role. Exception: a contributor may remove only its own finished
disposable scratchpad (see `.agents/AGENTS.md` Worktree lifecycle); never delete
peers’ live trees or the primary main checkout. Passing tests, committing, pushing, or opening
a PR is not completion: do not call integrated work complete until the PR is
merged, `origin/main` and local `main` agree, and the landing receipt names the
reviewed commit.

For implementation, debugging, architecture, or review, use the tracked
`.agents/skills/unigrok-workspace-memory/SKILL.md`. Recall against the Codex
worktree's own full HEAD, and record durable evidence only after `scripts/land`
certifies the exact commit.

## Codex chat continuity

At the start of every Codex chat rooted in this repository, read
`.codex/memory/context.md` and `.codex/memory/active-work.md` before planning or
acting. This project-local handoff is required even when chat history, browser
state, Chronicle, or contributor-memory MCP tools are available. Treat it as a
locator, not proof: verify drift-prone Git, CI, runtime, DNS, and cloud state
against their live sources.

Before ending a Codex chat with unfinished repository, deployment, release, or
external integration work, update `.codex/memory/active-work.md` with the exact
last verified state, remaining gates, and safety posture. Never put credentials,
tokens, OAuth codes, private keys, or other secret values in that file. When the
work is complete, replace the active handoff with a concise completed state so a
new chat does not resume obsolete steps.

## Public vs private intelligence

Process IP and harvest live in private `djtelicloud/unigrok-intelligence`. See [docs/design/public-private-git-split.md](docs/design/public-private-git-split.md).

## Cursor Cloud specific instructions

**Repo identity:** the GitHub repository is `djtelicloud/grok-mcp-server`. Product brand is UniGrok; Python package name is `mcp-grok`; CLI entrypoint is `unigrok-mcp`. The string `uni-grok-mcp` appears in agent docs/skills as an internal nickname only — do not treat it as the repo or package name.

**Stable gateway (dev):** prefer `uv run python main.py --http` (binds `127.0.0.1:4765`). `docker compose up --build -d` is the documented shared-service path and is required for the baked Grok CLI binary / `grok-cli-auth` volume; pure HTTP-local cloud sessions do not need Docker for API-plane work.

**Credential planes:** live `agent` inference needs `XAI_API_KEY` in the server `.env` and/or an authenticated CLI plane. Placeholder `your_xai_api_key_here` is treated as missing by the app (see `src/utils.py`), but `GET /readyz` only checks that the env var is non-empty — use `grok_mcp_status` / `grok_mcp_discover_self` (or a real `agent` call) as the usable-plane gate, not `/readyz` alone.

**Commands:** dependency sync and tests/lint gates are in [CONTRIBUTING.md](CONTRIBUTING.md) / [README.md](README.md) (`uv sync`, `uv run pytest`, `uv run python -m compileall -q src evals main.py`). Ruff is available via the `dev` dependency group but is not the required land gate; the suite currently has many pre-existing ruff findings.

**UI:** Control Center test bench at `http://localhost:4765/ui/`; core product path remains MCP at `http://localhost:4765/mcp`.

**Cursor Automations:** PR Approver, Security Reviewer, and Bugbot Autofix must follow
`.agents/AGENTS.md` section **Cursor Automations** (Cursor-native mirror:
`.cursor/rules/cursor-automations-single-pass.mdc`) — single-agent serial pass, one
action per PR head SHA, no parallel subagent fan-out, no bot-echo retriggers. Security
Reviewer must not launch “all review modules in parallel.”
