# Codex Active Work

Last updated: 2026-07-12
Owner: Codex
Status: Principal-bound identity hardening and first modular extraction landed; no active gate

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, and cloud identifiers live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Completed state

- PR #48 merged to protected `origin/main` as
  `e854b6a4b1f35c5aeb8eab224589e692c5dab2e2`. Its exact reviewed,
  CI-green, Codex-approved, and locally landed head was
  `d5af1e12c4904383b9fb68e2f2530514cd16f177`.
- HTTP session namespaces now compose the authenticated OAuth subject or
  gateway-key alias with an encoded subordinate client label. `X-Client-ID`
  and `X-Caller` remain reporting labels and cannot become security
  principals.
- HTTP budgets use exact authenticated-principal keys. Telemetry preserves
  client attribution as `principal|encoded-label`, while cost aggregation
  accepts only the exact principal or its label suffix. Crafted labels cannot
  evade or poison another principal's budget.
- Long provider subjects receive collision-resistant normalization. Existing
  HTTP sessions move to a principal-prefixed namespace on first post-upgrade
  use; stdio behavior is unchanged. A shared static gateway key intentionally
  remains one shared principal.
- `SECURITY.md` now supports `0.6.x`, and `docs/threat-model.md` defines the
  stable Core, remote Core, contributor Forge, and public-site trust zones.
- Test/lint tools are no longer Core runtime dependencies. `pytest`,
  `coverage`, and `ruff` live in the explicit `forge` extra and development
  group; the contributor Docker image continues to install them because Swarm
  executes those tools at runtime.
- PR #49 merged to protected `origin/main` as
  `03be09a5f86ed146c0207e720f6f65d20988929f`. Its exact reviewed,
  CI-green, Codex-approved, and locally landed head was
  `18b43c420832dc9b5b8960846a120a4597622287`.
- `src/identity.py` is now the sole definition site for request identity,
  principal context, session composition, and telemetry caller parsing.
  Production consumers import it directly; `src.utils` re-exports the same
  objects for compatibility. Tests pin all four ContextVar objects by identity.

## Verification

- The PR #48 landing gate printed
  `LANDED TO MAIN: d5af1e12c4904383b9fb68e2f2530514cd16f177`
  after 1,162 tests plus a rebuilt and smoke-tested contributor container.
- The PR #49 landing gate printed
  `LANDED TO MAIN: 18b43c420832dc9b5b8960846a120a4597622287`
  after 1,163 tests plus a restarted and smoke-tested contributor service.
- Python 3.11/3.12 CI, Project Site, standalone control image, Docker, offline
  evals, CodeQL, and exact-head `Codex Approval` passed on both PR heads.
- Wheel metadata confirms Core excludes `pytest`, `coverage`, and `ruff`; the
  `forge` extra contains all three. OKF generation and package builds passed.
- The initial exact-diff Grok security review returned only a planning stub, so
  no Grok approval is claimed for PR #48. Grok's exact-diff review of PR #49
  found no import-cycle, duplicate-context, or compatibility regression.

## Deliberately separate work

- Continue splitting `src/utils.py` and `src/http_server.py` by bounded context
  in small behavior-preserving PRs. Do not combine provider, storage, routing,
  and HTTP decomposition into a single high-blast-radius change.
- A top-level package migration from generic `src` to `unigrok` remains a
  release-planned compatibility change, not part of the identity security fix.
- Signed route receipts, routing counterfactual experiments, and an external
  penetration test remain product/security milestones rather than blockers for
  the local single-operator default.
- The previously landed public OKF intelligence payload bundle remains valid;
  this work did not release a new MCP package, deploy the consumer runtime,
  migrate SQLite, or activate Insider producers/promotions.
