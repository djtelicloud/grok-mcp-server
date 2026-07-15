# Codex Active Work

Last updated: 2026-07-15
Owner: Codex
Status: GitHub maintainer sweep complete; Control rollout awaiting Cloud Run retry; Stage 1 live generation and training remain blocked

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

- Protected PRs #127, #131, #132, and #133 are merged. They repaired the stale
  CodeQL/default-branch findings, integrated the patch-equivalent contributor
  backlog, hardened Cursor Cloud service-token scopes and introspection, and
  removed the final clear-text-logging data-flow pattern. The final security
  repair passed 1,987 tests and `scripts/land` certified exact head
  `58b0fa722c19a51d9d12355d3937676b467dc206` before protected merge.
- Superseded PRs #121, #124-#130 were closed with evidence; their obsolete
  remote branches and two older fully merged branches were deleted. No open PR
  or unresolved review thread remains. Contributor worktrees and every remote
  branch with unique commits were preserved.
- Local `main` and protected `origin/main` agree at merge commit
  `026078bd92fb2afd488ec371f3508bfba8f0bd30`. Exact-main CI and all three
  CodeQL analyzers passed; code scanning, Dependabot, and secret scanning have
  no open alerts.
- The Control Center image for merged auth head `cc8cc064fc42` was built as
  digest `sha256:7a932052851edfaf9b5fdfe7945db313d5c4a543cf542dac35efab59ed854c6a`.
  A zero-traffic `us-east1` candidate is still in provider-managed transient
  retry with no container logs; production traffic remains on the prior healthy
  revision. Do not shift traffic until the candidate reports Ready.
- Production `https://control.grokmcp.org/`, OAuth metadata, the protected
  `/control` redirect, and inactive-token introspection remain healthy. The
  existing revision and image are the rollback path.
- Issue #65 remains the only open issue. Its comments and the exact-head gate
  still conflict about Stage 1 authority, so further provider generation,
  dataset writes, training, and sealed evaluation remain blocked.
- Release and source versions agree at 0.6.0. The empty GitHub Wiki remains
  enabled even though product docs forbid
  a separate Wiki; changing that repository setting still requires an explicit
  maintainer decision.
