# Codex Active Work

Last updated: 2026-07-15
Owner: Codex
Status: GitHub and security are clean. The remote MCP and Control Center are
live in `us-central1` with east-region rollback assets. Stage 1 live generation
and training remain blocked.

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

- Protected PRs #148-#154 are merged. They hardened maintainer integration,
  made runtime markers tree-aware and portable, warned about the unrelated
  public `mcp-grok` package, corrected RFC 9728 challenge metadata, and made a
  missing OAuth bearer fail before remote introspection. They also recorded the
  regional recovery contract and refreshed this handoff. The final repair passed
  2,019 tests and `scripts/land` certified exact content head
  `615c70404390e2ec3c624f2b8ea5215f8a94cac7` before protected merge.
- Local `main` and protected `origin/main` agree at merge commit
  `2bf6647384922aa1b9e6162e645bd37560936133`. Exact-main CI and all three
  CodeQL analyzers passed. At the sweep snapshot before the current handoff
  repair, there were no open PRs, issues, discussions, review threads,
  code-scanning alerts, Dependabot alerts, secret-scanning alerts, or draft
  private advisories.
- Merged task worktrees, local task branches, remote `codex/*` branches, and
  stale local remote-tracking refs are removed. The shared checkout is clean;
  its runtime source marker is tree-equivalent to `main`; stable and contributor
  services are ready.
- Remote MCP runtime image digest
  `sha256:5b66e410262a127a8245bebc36ea34e1e90fc2a605ade2e31310e42030264b32`
  was built from application head `0d64a49ccf593780bbc80c00f4a419d1e413e0ef`
  and is live on ready revision `unigrok-remote-mcp-0d64a49` in `us-central1`.
  The global backend points only to the central regional NEG. Repeated public
  `/healthz`, `/readyz`, protected-resource metadata, and unauthenticated MCP
  probes pass; the challenge advertises the exact metadata document URL.
- `us-east1` route activation repeatedly stalled before container startup with
  a provider internal error, including on an isolated probe. The exact image
  started successfully in `us-central1`, proving the code, image, IAM, and
  secret bindings. East is detached from the load balancer and restored to its
  prior healthy revision as a rollback asset. Personalized Service Health is
  now enabled; it reports no active Cloud Run incident.
- The Control Center image digest
  `sha256:0a38494d0ebc01475ba168da4e8ed921b55d7f0d2d8de50646d5b407ccb2ca15`
  is live on ready revision `unigrok-control-center-eb454cd` in `us-central1`.
  Its site source tree is unchanged through current `main`. The URL map points
  the control hostname to central-only backend
  `unigrok-control-center-backend-central`; repeated project API, homepage,
  discovery, `llms.txt`, and OAuth contract probes pass, and central request
  logs confirm service of the public hostname.
- Removing the east NEG from the active backend produced a transient provider
  propagation gap and was rolled back automatically. The successful cutover
  instead staged an independent central backend and atomically changed the URL
  map. The prior `unigrok-control-center-backend` is now east-only and preserved
  with healthy revision `unigrok-control-center-ab8d77b` as the immediate
  rollback target. The newer east candidate remains at zero traffic because of
  the provider-managed activation failure.
- Release, source, and plugin versions agree at 0.6.0. Publishing a new release,
  accepting the `google-genai` 2.x migration, or changing the enabled empty
  GitHub Wiki remains a maintainer decision. Stage 2 generation, dataset writes,
  training, and sealed evaluation remain blocked by their existing exact-head
  authorization gates.
