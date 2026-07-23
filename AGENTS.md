# Public Repository Rules

- This repository is the public, workspace-neutral UniGrok core.
- Preserve the proven product boundary: one Grok-led `agent` harness, separate CLI
  subscription and xAI API planes, live model discovery, explicit billing receipts,
  and bounded cross-plane recovery only when the caller requests it.
- Never add private intelligence, subordinate-provider orchestration, IDE state,
  credentials, or user data.
- Never add symlinks, nested repositories, copied `.git` directories, or worktree
  pointers.
- The repository root is the main checkout. IDE-created linked worktrees belong outside
  this checkout under the parent `grok-mcp/.worktrees/` directory and must not be moved
  manually.
- New public tools require a clear public-use reason, boundary tests, and verification
  from a clean container.
- Preserve CLI isolation in `unigrok_public.server`: disposable empty workspace,
  disposable configuration home, OAuth-only subprocess environment, and denied local
  file/shell/edit/MCP capabilities.
- Factory key homes: Ground `../.env` (repo root); Sky/Space under `~/.docker/agentixos/*`. Never `grok-mcp-server/.env`.
- Pass only `XAI_API_KEY` into the API process. Never pass it to the CLI child, never
  print it, and never place it in an IDE MCP configuration.
- Do not hard-code or allowlist Grok language model ids. Discover CLI and API catalogs
  independently and validate explicit selections against the chosen live plane. Media
  model overrides remain provider-defined.
- Needle must remain visibly inactive until a real optional shadow/reflex runtime,
  tests, promotion boundary, and provenance receipts exist. Do not convert design or
  eval artifacts into a false runtime claim.
- Never commit `.env`, OAuth files, tokens, logs, sessions, caches, or generated state.
- Before claiming completion, run `uv run pytest -q`, `uv run ruff check .`, build the
  image, verify `/healthz`, `/readyz`, `/runtimez`, compare MCP `tools/list` with
  self-discovery, and exercise both configured credential planes.

## Cursor Cloud specific instructions

This is a single Python 3.12 service (`grok-mcp-server` / `unigrok_public`) managed by
`uv`. Dependencies are refreshed by the startup update script (`uv sync --frozen`); the
committed `.venv` is already present. The standard lint/test/build check suite lives in
`docs/development.md` — reference it rather than re-deriving commands.

- Run the server in dev mode directly (no Docker needed):
  `UNIGROK_HOST=127.0.0.1 PORT=4765 uv run python -m unigrok_public.server`.
  Non-obvious: the direct run defaults to `127.0.0.1:8080`, but `scripts/smoke_mcp.py`
  and the docs assume `4765`, so set `PORT=4765` to match. Docker/compose is NOT
  installed in this VM, and `docker compose build` downloads the `grok` CLI installer
  from `x.ai`, so it needs network egress.
- Credential planes gate live Grok answers, not the server itself. With no
  `XAI_API_KEY` and no `grok` CLI OAuth login, the server still starts, `/healthz` is
  `ok`, `/runtimez` works, MCP `tools/list` returns all 29 tools, and the local-state
  tools (`remember_fact`, `search_knowledge`, `list_sessions`) work end-to-end. But
  `/readyz` reports `not_ready`, `bootstrap.can_chat` is false, and the `@grok` `agent`
  tool cannot reach a plane. `scripts/smoke_mcp.py` (no `--invoke-*` flags) therefore
  stops at the `can_chat` assertion until a plane is configured; its `--invoke-cli/api/
  research/code` flags require live credentials. Provide `XAI_API_KEY` (metered API
  plane) or run `grok login --device-auth` to exercise the full `@grok` flow.
- Durable state is embedded SQLite at `~/.local/share/unigrok/public-state.db` by
  default (override with `UNIGROK_STATE_PATH` / `UNIGROK_STATE_DIR`); no external DB,
  cache, or queue service exists.
- Known pre-existing failures on `main` (independent of environment setup; do not treat
  as regressions you introduced): `uv run python scripts/check_release_contract.py`
  fails (`example.env` missing Compose vars `XAI_API_KEY_UNIGROK_GROUND`,
  `XAI_PLANE_API`) — CI on `main` fails here, before pytest runs. `uv run pytest -q`
  has ~26 failures in `tests/test_local_plane_m1.py`, `tests/test_local_plane_m3.py`
  (test `seed_ready` raw-inserts `gate_manifest('promote_gates')` which
  `PublicStateStore.initialize()` already dogfood-seeds → UNIQUE conflict) and
  `tests/test_remote_boundary.py::test_principal_key_selection_never_crosses_oauth_tenants`
  (expects source `owner_default`, code returns `owner_default:XAI_API_KEY`). Ruff and
  the ~495 other tests pass.
