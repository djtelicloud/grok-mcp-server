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
```

## Test beside an existing stable service

Stable may remain on port `4765`; run the candidate on `4775` without changing the
public default:

```bash
UNIGROK_PORT=4775 docker compose --env-file .env up --build -d grok-mcp
curl -fsS http://127.0.0.1:4775/healthz
curl -fsS http://127.0.0.1:4775/readyz
curl -fsS http://127.0.0.1:4775/runtimez
```

Then open a real IDE MCP client against `http://127.0.0.1:4775/mcp` (header
`X-Client-ID` as needed). Before release, compare MCP `tools/list` with
`grok_mcp_discover_self`, exercise both configured credential planes, and confirm
host sources match the running container for `src/` and static UI files.

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
