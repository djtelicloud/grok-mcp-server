# Codex Active Work

Last updated: 2026-07-14
Owner: Codex
Status: Maintainer sweep complete; Stage 1 live generation and training remain blocked

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

- Local `main` and `origin/main` are synchronized at
  `5e5d921a0bc6f102df4469f4ce9cc7a37b2bde7e`.
- The landing receipt printed `LANDED TO MAIN` after 1,564 tests. Main CI and
  CodeQL are green at that exact commit.
- The landed repair prevents release-hygiene tests from scanning ignored local
  IDE worktree metadata. The pre-existing untracked `scratch_swarm/` remains
  untouched.
- Open draft PRs #64 and #73-#76 remain review-required. PR #73, #75, and #76
  have current unresolved review feedback; no external thread was changed.
