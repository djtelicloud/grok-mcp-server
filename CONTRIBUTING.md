# Contributing to UniGrok

Thanks for helping. Two ways to contribute: report what you hit, or send a change.

## Reporting

Open an issue with the matching template (bug, feature, docs). For bugs in the
depth/level paths — `max`, `ultra`, hive voting, plane fallback — paste the receipt
fields from the tool result (`resolved_plane`, `fallback_occurred`, `resolved_depth`,
`usage`, `hive.stages`). Receipts are designed to make these reports reproducible;
redact anything sensitive before posting.

Security issues go through [SECURITY.md](SECURITY.md), not public issues.

## Sending changes

1. Fork and branch from `main`.
2. Run the local checks (see [docs/development.md](docs/development.md)):

   ```bash
   uv sync --frozen
   uv run pytest -q
   uv run ruff check .
   docker compose config --quiet
   ```

3. If the change is behavior-visible, add a line to the `[Unreleased]` section of
   [CHANGELOG.md](CHANGELOG.md) (Added/Changed/Fixed/Security/Documentation as fits).
4. Open a PR with a short description of the problem and the change. Keep PRs
   single-purpose — small ones land fast.

Every PR gets CI plus an automated `@grok` review pass; you can re-trigger a review by
commenting on the PR (see [docs/reference.md](docs/reference.md) for the workflow).
Address or answer review comments before merge — disagreement with the bot is fine when
you say why.

## Scope

This repo is the public, credential-free gateway: server, dashboard, docs, packaging.
Feature ideas that need private infrastructure will be redirected to an issue discussion
first.
