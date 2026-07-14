# Contributing to UniGrok MCP

Thanks for helping improve UniGrok. Prefer small, verified changes that keep the
shared Grok MCP gateway reliable.

**Public users** should follow the root [README.md](README.md) only. This file is
for **insiders**: people developing UniGrok itself (admins and GitHub write+
collaborators such as Curtis). Product language freeze:
[docs/design/public-vs-insider-surfaces.md](docs/design/public-vs-insider-surfaces.md).

## Surfaces you must not confuse

| Surface | Port / URL | Who | Purpose |
|---|---|---|---|
| **Stable Core** | `http://localhost:4765/mcp` · `/ui/` | Everyone | Public product MCP + machine-owner status UI |
| **Contributor Forge** | `http://localhost:4766/mcp` (dev compose) | Insiders | UniGrok checkout attach, Swarm, workspace memory |
| **Cloud control** | `https://control.grokmcp.org` / site `/control` | Insiders (GitHub OAuth) | Repo-facing control; **no** secret proxy, **no** local MCP tunnel |
| **IDE chat** | Core MCP `agent` | Everyone | **Primary** `@grok` path for public and insiders |

### LIVE vs TARGET (do not over-claim)

| Topic | LIVE today | TARGET |
|---|---|---|
| Core UI `/ui/` | Health, cost ledger, plane status, pasteable IDE actions | Observe + paste only; IDE MCP is the chat path |
| Cloud Console entry | GitHub OAuth + live write+ collaborator check (`write` / `maintain` / `admin`) | Same; always fail closed |
| Swarm | Contributor mode; prefer `dry_run` | Same; never default-apply |
| Land / merge | Codex/`scripts/land` + required exact-head Codex Approval | Unchanged; Grok review is advisory only |

## Local setup (insider)

```bash
uv sync
uv run python main.py init
```

Set `XAI_API_KEY` in `.env` for API-plane calls, and/or authenticate the CLI
plane:

```bash
docker compose up --build -d
docker compose run --rm grok-cli-auth
```

### Stable Core (always)

```bash
docker compose up --build -d
curl -s http://localhost:4765/healthz
curl -s http://localhost:4765/readyz
# Optional: http://localhost:4765/ui/
```

### Contributor Forge (insider only)

```bash
docker compose -p grok-mcp-dev -f docker-compose.dev.yml up --build -d
curl -s http://localhost:4766/healthz
```

Forge mounts the **UniGrok** checkout. Never point it at a random customer app
as if that were the public product path. Public agents must keep using **4765**.

## Development commands

```bash
uv run pytest
uv run python -m compileall -q src evals main.py
docker compose config
```

## Console / verification culture

Insiders should **visually** confirm Core health (and Forge when used) before
asking agents to open draft PRs — even when local tests passed. Prefer:

1. `/readyz` green on Core
2. Control Center planes ready
3. Focused pytest for the change
4. Draft PR with exact head SHA + attribution trailers

Do **not** build a second daily Grok chat in the browser. Chat is IDE → MCP.
Cloud control must never receive `XAI_API_KEY` or reverse-proxy local MCP.

Swarm and other power tools: default to dry-run / non-apply until you mean to
mutate. Pasteable terminal prompts for agents beat multi-step browser forms.

## Pull request guidelines

- Keep changes scoped to one behavior or feature.
- Open contributions as pull requests; do not push directly to protected
  `main`.
- Record the exact head commit SHA in the handoff and refresh review evidence
  after every new commit. A review of an older head is stale.
- Add or update tests for new tool behavior, CLI behavior, and runtime fixes.
- Keep credentials out of commits. Use `.env`; never commit real API keys.
- Prefer existing helpers and architecture over new parallel abstractions.
- Update `README.md` (public) and this file / design docs when behavior changes
  audience-facing setup.
- Run `uv run pytest` before opening a PR.

Human contributors and coding agents use the same evidence contract: intent,
changed paths, verification commands, human sponsor, agent provenance, risks.
Use `Agent-Assisted-By:` / `Agent-Reviewed-By:` per
[docs/agent-attribution.md](docs/agent-attribution.md). Grok review is advisory;
it does not authorize a merge. The Codex/project-admin role reviews the current
head and owns landing, merge, tag, release, and deployment. See
[ADR 0001](docs/adr/0001-cloud-control-plane-governance.md).

Codex approval is a required commit status bound to the exact PR head. Only the
repository owner may dispatch `.github/workflows/codex-approval.yml`. Any new
commit requires a new approval dispatch.

Security vulnerabilities: [SECURITY.md](SECURITY.md), not public issues.

## Multi-agent coordination

Keep the shared checkout on integrated `main`. Experimental work lives in
agent-prefixed worktrees (`codex/…`, `claude/…`, `gemini/…`, `grok/…`).

Handoffs must include branch/PR, full SHA, paths, tests, risks, sponsor, and
provenance. Contributors may push only their own agent-prefixed branch and open
or update its draft PR. Do **not** run `scripts/land`, merge, push shared
`main`, publish releases, or delete others’ worktrees unless you are the
explicit Codex/project-admin integration session.

## Further reading

- [docs/design/public-vs-insider-surfaces.md](docs/design/public-vs-insider-surfaces.md)
- [docs/ide-setup.md](docs/ide-setup.md)
- [architecture.md](architecture.md)
- [docs/threat-model.md](docs/threat-model.md)
