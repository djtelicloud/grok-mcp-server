# Developing UniGrok Public

This guide is for contributors and release verification. Ordinary users only need the
README.

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
UNIGROK_PORT=4775 docker compose --env-file ../.env up --build -d grok-mcp
uv run python scripts/smoke_mcp.py \
  --url http://127.0.0.1:4775/mcp \
  --invoke-cli \
  --invoke-api
```

Verify team-state persistence across a restart:

```bash
uv run python scripts/smoke_team_harness.py --url http://127.0.0.1:4775/mcp
docker compose restart grok-mcp
uv run python scripts/smoke_team_harness.py \
  --url http://127.0.0.1:4775/mcp \
  --verify-existing \
  --cleanup
```

Before release, also compare MCP `tools/list` with `grok_mcp_discover_self`, verify
`/healthz`, `/readyz`, and `/runtimez`, exercise both configured credential planes, and
test from a real IDE opened on an unrelated project.

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
