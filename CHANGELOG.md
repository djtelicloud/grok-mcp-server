# Changelog

All notable changes to the public UniGrok gateway.

## [Unreleased]

### Added
- Experimental `gemmagrok-local` Compose profile and standalone MCP helper for an
  explicitly selected, operator-owned local model runtime. The helper is loopback-only,
  exposes `chat`/`status`, receives no Grok credentials, and is not part of automatic
  `@grok` recovery.

### Changed
- Runtime limits that formerly behaved as fixed constants are now clamped environment
  controls and are reported by discovery/runtime receipts: agent sync window and turn
  cap, mission lease TTL, semantic-router output, prompt/workspace size, concurrency,
  file/media deadlines, file-content size, and terminal-state retention.
- Mission V2 freezes both governor configuration and verification mode. Ordinary
  generation can CommitDone structurally; run/test/prove outcomes require independent
  evidence. Callers may provide typed, pre-candidate `caller_evidence`, while candidate
  hashes and candidate-projection references remain forbidden as self-evidence.
- De-overfitting pass (hive-merged plan in `docs/DEOVERFIT.md`): physics envelope
  stub; governor magic numbers moved into versioned `WEIGHT_BUNDLE`; mechanism
  tests (`inspect.getsource`, `_JOB_TASKS` shape, semaphore identity) replaced
  with behavioral contracts. Needle remains inactive by default.

### Fixed
- Mission ownership and poll truth are generation-fenced end to end: claims are atomic,
  active quanta heartbeat, envelope/artifact/event/evidence writes require the exact
  owner, stale sweeper snapshots cannot revoke renewed leases, and job/autonomy mirrors
  require matching status, checkpoint version, and generation. Provider failures return
  retryable mission truth; terminal failures cannot resurrect through legacy tokens.
- Durable state recursively redacts structured secrets, stores only a bounded Mission V2
  projection, refreshes terminal-result retention age on completion, and prunes terminal
  mission compatibility rows. Session history is CommitDone-gated and idempotent by job,
  so rejected or replayed candidates do not enter memory.
- Router/hive receipts preserve actual planes, models, and costs even when a structured
  vote fails to parse; frozen mission governor settings now control resumed effort,
  depth, voter count, and turn budget.
- Billing receipts now preserve known cost and token usage across non-answer recovery,
  cross-plane fallback, router/hive/polish failures, state/projection errors, and Mission
  V2 restarts. Mission billing is cumulatively fenced by lease generation, and a
  post-provider error reclassifies one telemetry row instead of counting the spend twice.
- Mission shadow governor risk classifier: classify task/acceptance text for
  concurrency, security, irreversible, and adversarial-review signals; floor
  cognition to high/xhigh with engineer+architect+QA+security instead of the
  previous hardcoded low/engineer-only path. Continue envelopes now expose an
  explicit `reattach.continue_token` argument hint for hosts whose tool cache
  omitted the parameter.
- Split xAI API concurrency pools (B1): file/catalog reads use
  `UNIGROK_API_MAX_FILE_INFLIGHT` (default 2); metered generation/mutations keep
  `UNIGROK_API_MAX_INFLIGHT` (default 4). Slow `list_files` no longer HOL-blocks
  chat/search/media.
- Durable jobs outlive the MCP request (A1/P0): provider work is tracked in an
  app-scoped `_JOB_TASKS` set, awaited with non-cancelling `asyncio.wait`, and
  only cancelled via explicit `cancel_job` or bounded `shutdown_jobs` on
  lifespan teardown. Sync-window expiry returns pending without tearing down
  in-flight provider calls.

### Added
- Optional mission controller v2 (`UNIGROK_MISSION_V2`, default **off**, requires
  autonomy): durable `verifying` CommitDone, fenced leases with generation bump
  on release, sealed artifact hashes with redacted projections, typed evidence
  (candidate text is never evidence), shadow governor/council receipts, and
  sweeper requeue that skips mid-verify rows.
- Optional long-running autonomy spine (`UNIGROK_AUTONOMY`, default **off**):
  deadline quanta with `continue_token`, request-snapshot resume, append-only
  ledger, and ProposeDone → structural checker → CommitDone. Distinct durable
  statuses: `running` / `complete` / `error` / `needs_continuation`.

### Fixed
- Autonomy state-machine correctness: API jobs stay `pending` (not `continue`);
  review enrichment never downgrades terminal SQLite rows; claim leases are
  released; continue restores the immutable request snapshot; exception text is
  redacted; artifact hashes match stored normalization; session-lock pruning
  removed (dual-lock race); file download refuses missing size metadata.

### Fixed
- Failed durable `agent` jobs now persist a terminal `status=error` payload so
  `agent_result` no longer misreports a service restart while SQLite still said
  `running`.
- `review_pull_request` metadata survives `agent_result` polls (in-memory and
  SQLite pending enrichment).
- Compose passes through API timeout/concurrency and file-content hard-cap env
  vars documented in `example.env`.
- `get_file_content` refuses oversized files via metadata before download and
  prefers a bounded stream read when the SDK object supports it.
- Grok Build init/auth failures close and uncache the worker instead of leaving
  a live-but-unusable process.
- Idle session locks are pruned past a soft cap; `chat` and `xai_delete_file`
  use the durable job contract.
- Added `.dockerignore`; `ruff check .` is clean with test/eval per-file ignores.
- Added `CONTRIBUTING.md`; runtime smoke and the optional in-tree GitHub review
  workflow are documented against files that exist in this tree.

### Added
- `grok_mcp_onboard_client` installs a public **unigrok-visuals** skill pack for
  every client alongside `using-unigrok`: one capability-ladder core (markdown →
  Mermaid/SVG → host-native rich surface → hosted artifact) plus a thin adapter
  per host (Claude Code, Codex, Antigravity, Cursor, GitHub Copilot,
  generic/Grok), emitted into each host's native skill or rules location at
  global and project scope.

### Security
- Control center (`/ui`) responses carry a per-response script nonce and a
  stricter Content-Security-Policy; a new `SecurityHeadersMiddleware` adds
  baseline hardening (X-Frame-Options, nosniff, COOP/CORP, permissions-policy,
  default-deny CSP) to every HTTP response that has no route-specific CSP.
- Cloud Run mode now fails closed behind Control OAuth introspection with exact
  tool scopes, pre-buffer authentication and bounded request bodies, origin checks,
  issuer-qualified tenant state, optional exact-principal budgets and credentials,
  and principal-only access to hosted xAI file accounts.
- Runtime discovery, MCP initialization instructions, WebMCP, and generated onboarding
  plans now report the hosted API-only/instance-local contract, preserve the remote OAuth
  URL, and tell callers to poll `pending` jobs instead of duplicating billed work.
- OAuth protected-resource metadata no longer links to a separate site whose setup text
  is not versioned with this runtime.
- The hosted review workflow no longer sends an ignored plane argument and documents its
  actual bounded 600-second service-token lifetime.

### Documentation
- Restore and update the authenticated remote-deployment runbook with the current OAuth,
  secret-version, tenant, instance-local-state, atomic-cutover, smoke, and rollback
  contracts; link the hosted pilot from README, reference, security, development, known
  limits, launch, and contributor guidance.
- Document the `review_pull_request` MCP tool and pollable review metadata in
  `docs/reference.md`.
- Document the `depth` compatibility parameter (`auto`/`deep`/`hive`) alongside the
  preferred `level` ladder in `docs/reference.md`.
- Contributor setup: `CONTRIBUTING.md` covers local checks (`pytest`, `ruff`,
  Compose config) and security reporting via `SECURITY.md`.
- `docs/known-limits.md`: what has limited soak in 1.1.0, which behaviors are
  expected rather than bugs, and how to report a depth-mode miss — linked from
  the README and `docs/reference.md`.

### Fixed
- Non-answer detection + one same-plane recovery + bounded cross-plane fallback now
  guard every prose-producing route, including the non-agentic fast paths (`chat`,
  hive merge, deep-mode polish) that previously accepted a preamble-only completion
  as `final_answer` (live repro 2026-07-17: `"I'll ground the checklist in the
  actual flow, then start the answer at '## Checklist'"` with no body shipped as
  the final reply). Bounded internal JSON votes opt out via
  `nonanswer_recovery=False` — a malformed vote still just drops.
- `is_nonanswer_completion` gains a generic bare-preamble guard: a short
  single-paragraph `"I'll/let me <verb> ..."` promise with no delivered body is a
  non-answer even when the verb is outside the curated action list. Clarifying
  questions, stated blockers (`I'll need X from you`), and delivered content after
  a delimiter are untouched.
- `chat` now uses `cross_plane` fallback, matching the documented
  `one_same_plane_retry_before_bounded_api_fallback` contract that `agent` follows.
- Deep-mode final polish failures no longer discard the already-good answer; the
  unpolished text ships instead.

### Changed
- Antigravity auto-approve now uses `globalPermissionGrants.allow` with per-tool
  grants (`mcp(grok/agent)`, `mcp(grok/agent_result)`) — the battle-tested pack
  format — instead of whole-server `trust: true`. Global scope targets
  `~/.gemini/config/config.json` under `userSettings`, project scope
  `.gemini/settings.json`. The trust flag remains as a documented fallback for
  Gemini CLI, which has no permission grants.

## [1.1.0] - 2026-07-17

### Added
- GitHub Copilot client onboarding: gh Copilot CLI `~/.copilot/mcp-config.json`
  merge entry (repo-level `.copilot/mcp-config.json` at project scope, `.vscode/mcp.json`
  alternative for VS Code), a namespaced `.github/instructions/unigrok.instructions.md`
  routing rule, and `--allow-tool 'grok(agent)'` session flags for auto-approve.
- Per-IDE "never prompt for @grok" auto-approve in the onboarding plan, each via its
  native mechanism: Claude Code `permissions.allow` (per-tool `mcp__grok__agent`), Codex
  `config.toml` MCP tool `approval_mode = "auto"`, Gemini/Antigravity server `trust: true`,
  Cursor the beforeMCPExecution hook. GitHub Copilot/generic get none (no verified format).
- Cursor client onboarding: `grok_mcp_onboard_client` now emits a `.cursor/mcp.json` merge entry (points Cursor at the Grok gateway with `X-Client-ID: cursor`, carries no credentials), a `.cursor/rules/using-unigrok.mdc` routing rule, and a `.cursor/hooks.json` + `before-unigrok-agent.py` beforeMCPExecution hook that auto-approves ONLY the `agent` tool so `@grok` never stalls on a permission prompt. Cursor is a client, not an execution plane — ported from the old public version's static `.cursor/` setup.
- Depth modes `deep` (cached j-space harness prompt, harness-leak guard, final polish loop) and `hive` (draft → parallel persona votes with numbered-line dif-vote anchors → always-on xhigh merge; voters split across CLI/API planes; per-stage plane+cost receipts)
- Public level ladder `none`→`ultra` via `level` parameter and `voters` override
- Auto++ router: three CLI-first parallel intent votes (route/depth/voter-count)
  replacing the semantic API pass on most unclear tasks, with actual-plane receipts and
  dynamic voter sizing
- Agent job persistence to SQLite (recorded results survive restarts; interrupted
  generic jobs return status `"lost"` with an unknown provider outcome)
- Verifying benchmark suite: `benchmark_deep.py` (executed-code checks, level sweeps, `--voters` sweep), `persona_bench.py` tournament, `parallel_probe.py`, `triage_optimize.py` scout
- Dogfood optimizer loop (`dogfood_optimize.py`) with anti-Goodhart counter-metric gate (no new imports, bounded diff) and 8% noise floor
- Six gateway functions hive-optimized with measured wins (+52.8%, +22.6%, +20.8%, +18.9%, +18.0% ×2)
- Shadow done-vote experiment flag `UNIGROK_SHADOW_DONE_VOTE` (off by default)
- Metered-API output cap on micro-emit votes (`UNIGROK_VOTE_MAX_OUTPUT`, default 128)

### Changed
- `xhigh` effort auto-downgrades to `high` on the API plane instead of erroring
- Requested/resolved depth and level echoed in payloads and telemetry

### Fixed
- CLI-plane tool-permission cancellations (grok CLI 0.2.101 lowercase tool IDs; ACP `session/request_permission` reject handler; one tool-less retry in-session) that silently converted subscription work into metered API spend via cross-plane fallback

## [1.0.0] - 2026-07-17

Initial public candidate: dual-plane Grok harness (Build CLI subscription + metered xAI API), workspace-neutral courier model, sessions and durable facts in SQLite, media/search/code specialists, benchmark telemetry.
