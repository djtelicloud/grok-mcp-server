# Changelog

All notable changes to the public UniGrok gateway.

## [Unreleased]

### Documentation
- Document the `@grok review` PR workflow (maintainer comment trigger, read-only
  default-branch execution, job-level concurrency, hosted vs lab configuration) in
  `docs/reference.md`, with a matching README feature-table row.
- Document the `depth` compatibility parameter (`auto`/`deep`/`hive`) alongside the
  preferred `level` ladder in `docs/reference.md`.

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
- Auto++ router: three flat-rate parallel intent votes (route/depth/voter-count) replacing the metered routing pass on unclear tasks, with dynamic voter sizing
- Agent job persistence to SQLite (results survive restarts; interrupted jobs return status `"lost"`)
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
