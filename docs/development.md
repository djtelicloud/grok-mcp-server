# Developing UniGrok Public

This guide is for contributors and release verification. Ordinary users only need the
README. See also [CONTRIBUTING.md](../CONTRIBUTING.md).

Design notes (no runtime claims): [WASM × dogfood](WASM_DOGFOOD.md) — guest ABI and
trigger conditions; wasm is **not** in the shipping gateway today.

## Local checks

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
docker compose config --quiet
docker compose build grok-mcp
```

## Rebuild and runtime-test the local service

The checked-in Compose file intentionally uses one fixed container and the persistent
`unigrok-*` auth/state volumes. It is not a side-by-side deployment definition. Stop the
current container, then recreate the same local service on `4775` if you want to keep an
IDE's `4765` configuration untouched while testing:

```bash
docker compose stop grok-mcp
UNIGROK_PORT=4775 docker compose --env-file .env up --build -d grok-mcp
curl -fsS http://127.0.0.1:4775/healthz
curl -fsS http://127.0.0.1:4775/readyz
curl -fsS http://127.0.0.1:4775/runtimez
uv run python scripts/smoke_mcp.py --url http://127.0.0.1:4775/mcp
```

Then open a real IDE MCP client against `http://127.0.0.1:4775/mcp` (header
`X-Client-ID` as needed). Before release, compare MCP `tools/list` with
`grok_mcp_discover_self`, exercise both configured credential planes, and confirm
host sources match the running container for `src/` and static UI files.

Restore the normal port by recreating the same service with `UNIGROK_PORT=4765`.
Do not point two containers at the same SQLite state volume.

To verify team-state persistence across a restart, create a named `agent` session,
restart the container, and confirm the same session still resolves through the MCP
tools (facts / session history).

## Cutting a release

One version bump commit, then tag and publish — the version string lives in three
places that must move together:

1. Bump the version in `pyproject.toml`, `src/unigrok_public/__init__.py`, and the
   README version badge. Promote the `[Unreleased]` section of `CHANGELOG.md` to
   `## [X.Y.Z] — <date>`, leaving an empty `[Unreleased]` above it. Commit as
   `chore(release): X.Y.Z` and push (or merge via PR).
2. Tag and publish the GitHub release:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z - <release title>"
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title "vX.Y.Z - <release title>" --notes-file <notes>
   ```

3. The npm companion package `@djtelicloud/unigrok` is versioned in lockstep with
   gateway releases and republished by the maintainer at each tag. It prints verified
   setup steps; it is not the server itself.
4. Verify: the release page renders, `/healthz` on a rebuilt service reports the new
   version, and `npm view @djtelicloud/unigrok version` matches the tag.
