# Codex Active Work

Last updated: 2026-07-16T18:04:37Z
Owner: Codex integration coordinator
Status: Current critical repairs are merged and Live; the security queue remains active.

This is the project-scoped handoff for new Codex chats. Verify drift-prone Git,
CI, runtime, DNS, cloud, and pull-request state live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Current integration state

- Protected `origin/main` and the locked local `main` checkout agree. Verify
  their exact SHA live; this handoff deliberately does not hard-code its own
  later documentation merge.
- PR #316 is merged. Installed CLI startup no longer imports caller-cwd dotenv
  policy, credential dotenv files are owner-only and validated before loading,
  credential-bearing xAI proxy origins are allowlisted, and write-token
  workflows pin third-party actions to immutable commits.
- PR #325 is merged. Child processes now scrub canonical and unknown
  secret-shaped environment credentials by default, preserve only the reviewed
  Claude OAuth exception, and configured caller budgets reject when spend
  accounting is unavailable. Exact-head CI, CodeQL, Codex Approval, Supervisor
  Approval, Security Reviewer, and Bugbot passed; the local suite passed 2,173
  tests.
- PR #335 is merged. Static bearer identities now use stable explicit IDs,
  legacy index-based keys fail-closed without an exact migration map, OAuth
  principals are issuer-bound and audience-checked, and hosted caller-budget
  keys use the same canonical principal form. Exact-head CI, CodeQL, Codex
  Approval, Supervisor Approval, Security Reviewer, Cursor Approval, and
  Bugbot passed; the local suite passed 2,195 tests.
- PR #338 is merged. Deferred research-job submission, tool reads, listing,
  and `grok://jobs/{id}` now use the full server-bound authenticated principal
  as the hosted ownership boundary; client labels remain attribution-only,
  foreign and legacy-unowned rows are denied, and trusted unbound local/stdio
  callers keep their historical open view. Exact-head CI, CodeQL, Codex Approval,
  Supervisor Approval, Security Reviewer, Cursor Approval, and Bugbot passed;
  the local suite passed 2,202 tests.
- Stable `:4765` and contributor `:4766` are healthy and ready on the current
  image with read-only root filesystems.
- Remote MCP is Live at 100% in both regions on image digest
  `sha256:ef73149f032b2f86ac7d3e1a46dd294a90074edd1ab54d9f39fa7153762faab7`:
  `us-central1` revision `unigrok-remote-mcp-00016-7d2` and `us-east1`
  revision `unigrok-remote-mcp-00020-vjp`.
- Public health, readiness, OAuth protected-resource metadata, and protected
  `/runtimez`, `/metrics`, and `/mcp` rejection probes pass. No error logs were
  found on the new Cloud Run revisions after rollout.

## Active queue and safety posture

- Python-superiority campaign PR #475 is held at exact head
  `ba2daa269956997fe28fe8449099f8eed53a519c`. Its contributor baseline at the
  prior head passed 2,202 tests, but two Forge tasks
  (`f29b306130e945f8a34eaa44b91fcb39` and
  `eca231fdad094aab9e909728f13035df`) failed before candidate generation
  because preflight stripped the repository's required `src.` package prefix.
  Grok's new unconditional keep-`src` commit is narrower than #476 and is not
  the approved repair. Its final scoreboard reclassification is accepted as
  honest bookkeeping: 0 measured wins, 1 held target, 77 plans, 133 skips.
  Independent reproduction also found the routing task's benchmark omits
  required keyword-only `reason_score`; its minimally corrected fixture has a
  19.37% noise floor. After CONTINUE, retry only the held Pareto target first.
  Sponsor permits Grok to improve the experiment method before rerun. Freeze
  that method on a clean current-main task branch before any production change
  or baseline capture, then apply it unchanged to original and candidate.
- Draft #476 repairs that Forge import-provenance bug. Codex's focused suite
  passed 27 tests and the full suite passed 2,204 tests. Normal Codex must
  independently review and land it, then refresh the Forge runtime before
  Grok receives an explicit `CONTINUE` comment on #475. Grok was told to hold
  its exact head and not open more plan PRs meanwhile.
- Draft #422 contains the durable campaign gate plus the separate Codex
  disposition for #475. Public results remain held. Any one-file-to-many-file
  candidate is one replacement bundle: compare the same public operation and
  report total bundle LOC, end-to-end latency, and peak memory; never aggregate
  per-file percentages into a result.

- Stable identity prerequisites are Live, but principal-storage publication
  remains blocked on its owner-scoped schema/migration, legacy-row quarantine,
  and cross-principal regression contract.
- Other ready security packets remain queued; rebase and integrate only after
  exact-head review and conflict checks.
- Provider-broker, stateful MCP sampling, Swarm, and shared multi-principal
  surfaces remain fail-closed until their recorded activation gates are met.
- Do not promote ready feature/product PRs merely because checks are green;
  coordinator authority covers system health and already-authorized work only.

## Scratchpad safety

- Codex removed the finished land-gate, distill-scope, compaction-fence,
  installed-startup, subprocess-security, research-job integration, and prior
  handoff worktrees only after proving each clean and merged or patch-equivalent
  to `main`.
- Codex removed the mount-free `grok-mcp-ci-local` verification container after
  the production local runtimes were healthy.
- Preserve the locked protected `main`, active Codex/Cursor/Claude/Grok
  worktrees, and the sponsor's primary Documents checkout.
- Before removing any future candidate, re-check task state, processes, dirty
  state, unique commits, and open task packets. Never rely on this handoff as a
  live lock.

## Product boundary

- Coordinator work is limited to integration health, safe repairs, deployment
  verification, and proven-orphan cleanup. Do not invent product priorities.
- Stage 2 remains fail-closed. Live generation, training, sealed evaluation,
  release publication, and material provider-policy changes require their
  existing explicit authorization gates.
