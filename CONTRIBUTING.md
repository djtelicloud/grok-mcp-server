# Contributing to UniGrok Public

Anyone can clone, run, and open a pull request. After you authenticate with GitHub
(locally or on the website), install the **public Ground pack** so your IDE can
orchestrate multi-step UniGrok work the same way maintainers do.

## What day-1 access includes (and does not)

| You get after GitHub auth + Core + onboard | You do **not** get automatically |
|--------------------------------------------|----------------------------------|
| UniGrok Core on `localhost:4765` | A second labor Docker seat (e.g. forge-style) |
| Public Ground pack (`using-unigrok` + `mission-brief-harness`) | Extra operator Docker nodes beyond Core |
| Optional soft `affiliation` if you are an official collaborator | Private operator skills or training pipelines |
| Mission Briefs with host-as-orchestrator | Auto-install of anyone’s full skill tree |

**Rule:** the public Ground pack is the **safe valuable start**. Heavier, private, or
multi-node capacity is granted **later** by a maintainer when (and only if) you run an
approved **extra node** under their map — not because you installed the public pack.

Public skill cream still lands only through normal product review (PR + maintainer
gate). Practice on Ground → densify findings → PR; do not expect private labor folders
to mirror into your IDE.

## After GitHub auth → install the public Ground pack

Do this once per machine (or after a clean IDE profile).

### A. Authenticate with GitHub

**Website (contributor control center)**

1. Open [Contribute](https://grokmcp.org/contribute) (or your deployed site’s `/contribute`).
2. Sign in with GitHub at [control.grokmcp.org](https://control.grokmcp.org).
3. Access is rechecked against your GitHub role on this repository — no xAI keys on the site.

**Local (git + GitHub CLI)**

```bash
# one-time (or when your token expires)
gh auth login
# confirm the login that will appear on PRs / collaborator checks
gh api user --jq .login
```

Use that same GitHub login for branch names and collaborator invites. Do not put
personal PATs into IDE MCP JSON.

### B. Run UniGrok Core on this machine

Follow the README “Get running in three minutes” path, then confirm readiness:

```bash
docker compose up -d grok-mcp
curl --fail --silent http://127.0.0.1:4765/readyz
```

You are ready when the JSON includes `"status":"ready"` (HTTP 200 alone is not enough if
the body is an error page or partial bootstrap). Prefer `127.0.0.1` over hostnames that
might resolve oddly on some networks.

**Windows (PowerShell):** Docker Desktop must be running (WSL2 backend is typical). If
`curl` is the PowerShell alias and misbehaves, call the real binary:

```powershell
docker compose up -d grok-mcp
curl.exe -fsS http://127.0.0.1:4765/readyz
```

Connect your IDE to `http://localhost:4765/mcp` (stable `X-Client-ID` per IDE).
Optional local bearer: see README “Optional local bearer protection”.
Never put `XAI_API_KEY`, personal PATs, or Grok session tokens into IDE MCP JSON —
credentials stay in the UniGrok service environment / CLI auth volume only.

### C. Install the public Ground pack (skills)

The **public Ground pack** means: your IDE stays the orchestrator; UniGrok `agent` is
leaf labor. The pack is two skills:

| Skill | Role |
|-------|------|
| `using-unigrok` | How to call `@grok` / `agent` safely |
| `mission-brief-harness` | Mission Brief template, try ≤3 loop, offline free path |

**From any connected IDE** (Claude Code, Cursor, Codex, Antigravity, Copilot, etc.):

1. Ensure the UniGrok MCP server is connected.
2. Call the tool **`grok_mcp_onboard_client`** (or accept the first-use offer).
3. Choose **`global`** (recommended) so skills land in your IDE user scope — not every repo.
4. Let the IDE preview files, approve the writes, then **reload** the session.
5. Confirm skills exist: `using-unigrok` and `mission-brief-harness`.

Example tool call when elicitation is not available:

```text
grok_mcp_onboard_client
  client: auto   # or claude_code | cursor | codex | antigravity | github_copilot | generic
  choice: global
```

**What “installed” looks like**

- Claude Code: `~/.claude/skills/using-unigrok/` and `…/mission-brief-harness/`
- Codex: `~/.codex/skills/using-unigrok/` (and companion)
- Cursor: user-scope skills such as `~/.cursor/skills/using-unigrok/` and
  `~/.cursor/skills/mission-brief-harness/` (Windows:
  `%USERPROFILE%\.cursor\skills\…`), plus any rule/hook paths from the onboard plan
- Other clients: paths returned in the onboard plan — follow the IDE’s preview

UniGrok never writes those files itself; the **calling IDE** installs after you approve.

### D. Optional: confirm contributor affiliation

If maintainers configured official-contributor detection on the service, after you are
authenticated as a GitHub collaborator (or on the allowlist):

```text
grok_mcp_status
# or
grok_mcp_discover_self
```

Look for `affiliation.is_official_contributor` (true/false/null). That is soft UX only —
not a secret vault. Service env for operators is documented in the README under
“Official GitHub contributors”.

### E. First multi-step task (Ground pack in practice)

Put a short **Mission Brief** in `agent`’s `task` (goal, options, constraints, done-when,
return shape). Prefer densified returns: WHAT / WHY / DELTA / NEXT. Details live in
skill `mission-brief-harness` and README “Multi-step agentic work (Ground pack)”.

### F. Later: extra operator node (maintainer grant only)

If a maintainer invites you to operate an **extra node** on your own machine, they will
issue that access separately (Docker map, keys, and any optional labor seats). That
grant is **not** part of this pack, not part of website GitHub login alone, and not
implied by `grok_mcp_onboard_client`. Until then, stay on Core + public Ground pack +
normal git PRs.

---

## Local checks

```bash
uv sync --frozen
uv run ruff check .
bash scripts/ci-insider-denylist.sh
uv run python scripts/check_release_contract.py
uv run python scripts/check_docs.py
uv run pytest -q
docker compose config --quiet
docker compose build grok-mcp
```

## Runtime smoke (manual)

Use an isolated candidate port and state volume when a normal `:4765` instance is
already serving work. The simplest clean-clone smoke is:

```bash
UNIGROK_IMAGE=unigrok:public-candidate UNIGROK_PORT=4775 \
  docker compose up --build -d grok-mcp
curl -fsS http://127.0.0.1:4775/healthz
curl -fsS http://127.0.0.1:4775/readyz
curl -fsS http://127.0.0.1:4775/runtimez
uv run python scripts/smoke_mcp.py --url http://127.0.0.1:4775/mcp
```

Compare MCP `tools/list` with `grok_mcp_discover_self`, then exercise every configured
route. A local smoke does not publish a GitHub release or deploy a hosted service.
Restore the normal port only after the candidate is stopped.

## Pull requests

- Keep changes scoped; match existing style and contracts.
- Keep the tree limited to the generic `README.md` clone/build/start/use
  contract. Private topology, provider sessions, raw evaluations, and operator
  runbooks belong outside this repository.
- Do not commit `.env`, OAuth tokens, or API keys.
- Prefer durable job semantics for slow or failure-prone MCP tools: terminal
  success **and** terminal error payloads must persist for `agent_result`.

## Security

Report vulnerabilities via `SECURITY.md`. Do not file public issues for secrets
or unauthenticated remote exposure.
