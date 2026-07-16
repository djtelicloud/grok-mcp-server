## Summary

<!-- What does this PR change and why? Link the issue it addresses, e.g. "Closes #1". -->

## Changes

<!-- Bullet list of the concrete changes. -->

## Head and handoff evidence

<!--
Exact head SHA reviewed/tested:
Accountable GitHub contributor:
Submitting agent-prefixed branch:
Changed paths:
Known risks, generated files, or follow-up work:
-->

### Agent and model provenance

<!--
Keep human accountability separate from tool/model credit. Add one row per
material assistant or advisory reviewer. Use `unverified` when the exact model
is unknown; do not infer it from a product name. Canonical commit trailers and
allowed values: docs/agent-attribution.md.
-->

| Role | Provider product | Model | Model source | Surface | Evidence |
| --- | --- | --- | --- | --- | --- |
| <!-- implementation/review/etc. --> | <!-- OpenAI Codex/etc. --> | <!-- exact or unverified --> | <!-- receipt/session/user-reported/unverified --> | <!-- IDE/gateway --> | <!-- optional receipt/comment --> |

## Fact-check checklist (required for docs & config changes)

Commands and names in this repo are frequently guessed wrong — please verify
each claim you make against the actual source:

- [ ] Endpoints match the code (health is **`/healthz`**, MCP is **`/mcp`**, UI is **`/ui/`** — see `src/http_server.py`)
- [ ] The Docker Compose service is named **`grok-mcp`** (see `docker-compose.yml`)
- [ ] CLI-plane readiness is probed with **`grok --check`** (see `src/utils.py`)
- [ ] Any environment variable you reference actually exists (`git grep <VAR_NAME>`)
- [ ] No destructive commands recommended without a warning (e.g. `docker compose down -v`, `rm -rf`, `git reset --hard`)

## Testing

- [ ] `uv run pytest -q` passes locally
- [ ] For docs-only changes: every command in the diff was actually run against a live checkout
- [ ] Results above apply to this PR's current head SHA (not an earlier commit)
- [ ] Any `@grok` review shown as current names this exact head SHA
- [ ] This PR comes from the submitting agent's task branch, not shared `main`

## Risk

Declare exactly one risk level in the PR body: `risk: low`, `risk: medium`, or
`risk: high`. Low/medium packets may use the authorized Cursor failover path
when `Supervisor Approval` is green. High-risk packets require exact-head
`Codex Approval`.

## Security

- [ ] No secrets, tokens, or machine-specific absolute paths in the diff
- [ ] Authentication, workflow, deployment, or landing changes include a
      fail-closed test and identify the trust boundary they change
- [ ] Contributor-controlled text is treated as untrusted evidence, not as
      workflow commands or model-authorized mutation

## Codex/project-admin landing gate

<!-- Checked by Codex/project-admin automation, not the contributor or user. -->

- [ ] Required CI and CODEOWNER review pass on the current head
- [ ] Codex disposition applies to the current head
- [ ] Required `Supervisor Approval` status is present on the current head
- [ ] High-risk packets also have exact-head `Codex Approval`
- [ ] Landing/merge receipt is captured by the currently operative integration path
