# Codex Active Work

Last updated: 2026-07-15
Owner: Codex
Status: Maintainer repair locally landed; protected PR merge pending; Stage 1 live generation and training remain blocked

This is the project-scoped handoff for new Codex chats. Verify drift-prone Git,
CI, runtime, DNS, cloud, and benchmark state live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Verified campaign state

- The Stage 1 gate is a deterministic, transport-free structural exercise: 30
  roots, 120 variants, 150 mechanically evaluated candidates, and 120 bounded
  mock role attempts across the six declared packs.
- Every prediction input carries its own TTL context. Exact request payloads,
  trusted scenarios, executable oracle results, effect receipts, terminal
  evidence, and confusion matrices are persisted in owner-private,
  content-addressed artifacts and reconstruct cleanly in a fresh process.
- Provider output cannot assign authority. Proposal validity, episode outcome,
  TTL validity, effect observation, and completion are derived mechanically.
- The attempt ledger is fail-closed under lease expiry, crash takeover,
  duplicate work, missing artifacts, and exact-boundary completion. Started or
  indeterminate work is never retried silently.
- Executor code and every callable dependency are bound before execution;
  post-bind injection is rejected before an attempt or injected call occurs.
- Promise-only and unsolicited plan-shaped completions fail closed as errors;
  unverified terminal text persists as `NULL`, explicit gateway failures as
  `0`, and only a future receipt-bound verifier may write `1`. Schema v14
  quarantines unsupported historical positives.
- Routing, local semantic evidence, the v2 cloud mirror, caller metrics, and
  status output all preserve the tri-state contract. Auxiliary history
  compaction is excluded from task outcome rates, and an existing explicit v1
  collection setting is safely redirected to v2.
- Local verification passed 150 campaign tests and 1,480 repository tests,
  plus Ruff, repository generation checks, JSON validation, and diff hygiene.
- No live provider call, dataset write, model download, training run, or sealed
  evaluation occurred while repairing or validating this gate.

## Remaining authorization gates

- Stage 2 is fail-closed. A new, bounded live Stage 1 manifest must separately
  specify provider, call, cost, time, privacy, retry, and artifact limits and
  receive Codex approval at its exact head before any live generation.
- Any generated dataset must pass the mechanical gates and another exact-head
  Codex review before training. Training and sealed evaluation require their
  own later authorization and must remain structurally separate.
- Stage 0.5 provider wiring uses Google ADC and the loopback UniGrok gateway;
  never copy user credential files or secrets into the repository.

## Latest maintainer sweep

- Local `main`, the contributor runtime source marker, and the pushed task
  branch are at the PR #127 repair series. Protected `origin/main` remains at
  its pre-merge head until the review gates pass.
- Draft PR #127 contains the minimal CodeQL and public OKF-link repair. Its full
  suite, generated-OKF check, Ruff, Docker, site, attribution, and CodeQL checks
  are green. Do not bypass its draft, Code Owner, Codex Approval, or
  protected-merge gates.
- PR #125 has two current unresolved review threads and a stale generated OKF
  failure. PR #126 has two current unresolved UI review threads while CI is
  green. Draft PRs #121 and #124 have no unresolved threads; hosted review
  smoke runs associated with #121 failed in the read-only review transport.
  No external thread was resolved or replied to.
- Issue #65 is the only open issue. Its latest comment reports a bounded live
  Stage 1 run, while this handoff and the issue body retain the exact-head
  authorization gate. Treat any further live generation or training as blocked
  until the authority state is reconciled explicitly.
