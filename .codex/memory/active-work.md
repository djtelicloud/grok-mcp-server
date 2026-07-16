# Codex Active Work

Last updated: 2026-07-16
Owner: Codex integration coordinator
Status: Current critical repairs are merged and Live; the security queue remains active.

This is the project-scoped handoff for new Codex chats. Verify drift-prone Git,
CI, runtime, DNS, cloud, and pull-request state live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Current integration state

- Protected `origin/main` and the locked local `main` checkout agree at
  `d87bd1473246893eae8e5960a4862e366619b59a`.
- PR #316 is merged. Installed CLI startup no longer imports caller-cwd dotenv
  policy, credential dotenv files are owner-only and validated before loading,
  credential-bearing xAI proxy origins are allowlisted, and write-token
  workflows pin third-party actions to immutable commits.
- Exact-head CI, CodeQL, Codex Approval, Supervisor Approval, Security Reviewer,
  and Bugbot passed for PR #316. The exact local suite passed 2,163 tests.
- Stable `:4765` and contributor `:4766` are healthy and ready on the current
  image with read-only root filesystems.
- Remote MCP is Live at 100% in both regions on image digest
  `sha256:3cbde9349bead22c133bd2a12a2ca59dfd372f4ce9b632aee322ffe6d6667f7e`:
  `us-central1` revision `unigrok-remote-mcp-00013-qfz` and `us-east1`
  revision `unigrok-remote-mcp-00017-5bp`.
- Public health, readiness, OAuth protected-resource metadata, and protected
  `/metrics` and `/mcp` rejection probes pass. No error logs were found on the
  new Cloud Run revisions after rollout.

## Active queue and safety posture

- Stable explicit bearer IDs, issuer-bound OAuth principals, and audience
  binding remain blockers before principal-storage publication.
- Caller-scoped research jobs and other ready security packets remain queued;
  rebase and integrate only after exact-head review and conflict checks.
- Provider-broker, stateful MCP sampling, Swarm, and shared multi-principal
  surfaces remain fail-closed until their recorded activation gates are met.
- Do not promote ready feature/product PRs merely because checks are green;
  coordinator authority covers system health and already-authorized work only.

## Scratchpad safety

- Codex removed the finished land-gate, distill-scope, and compaction-fence
  worktrees only after proving each clean and merged into `main`.
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
