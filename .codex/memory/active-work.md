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

- PR #163 merged the reviewed PR #162 policy work plus Codex repairs for generic
  rehydrate, Gemini cleanup timing, Claude root cleanup, and release-hygiene
  coverage. `scripts/land` certified exact content head
  `7f5a51005e713fdbe8e59849d88fc6bf303cff65` after 2,023 tests; protected
  merge commit `e10dd73c12d84f0a37bda6ebd9435f55e45ce398` is synchronized locally
  and remotely.
- Exact-main CI and all three CodeQL analyzers passed. The checked GitHub state
  has zero open PRs, issues, discussions, code-scanning alerts, Dependabot
  alerts, secret-scanning alerts, vulnerability alerts, or draft advisories;
  the remote has only `main`.
- Codex removed its finished integration scratchpad and branch. Preserve the
  active detached Codex thread worktree and Grok's `grok/fix-162-ready`
  worktree; neither is ahead of `main`.
- Stable `:4765` and contributor `:4766` are ready, and the runtime marker is
  tree-equivalent to `main`. PR #163 changed no server, container, dependency,
  MCP UI, or Control Center source, so no Cloud Run rebuild was required.
- Remote MCP is live at 100% on ready `us-central1` revision
  `unigrok-remote-mcp-7c7c30e`, image digest
  `sha256:a377d3c89cea616360cabe5cf162f5ba187fffd25a8541004cd13d32f5b03f81`.
  Public health, readiness, and OAuth protected-resource probes pass.
- Control Center is live at 100% on ready `us-central1` revision
  `unigrok-control-center-617e8cf`, image digest
  `sha256:7519dc2aafb8b06fc972ddd18f5a39cf5704976c7c341752f0814c8d4fe16242`.
  Homepage, public project API, and OAuth discovery probes pass.
- Healthy east rollback assets remain `unigrok-remote-mcp-00004-54c` and
  `unigrok-control-center-ab8d77b`; east is not an active global route.
- Release, source, and plugin versions agree at 0.6.0. Publishing a new release,
  accepting the `google-genai` 2.x migration, or changing the enabled empty
  GitHub Wiki remains a maintainer decision. Stage 2 generation, dataset writes,
  training, and sealed evaluation remain blocked by their existing exact-head
  authorization gates.
