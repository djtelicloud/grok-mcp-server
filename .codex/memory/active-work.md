# Codex Active Work

Last updated: 2026-07-16
Owner: Codex integration coordinator
Status: GitHub, security, local runtimes, and the hosted product are clean.
The contributor queue is empty. Stage 1 live generation and training remain
blocked behind their existing exact-head authorization gates.

This is the project-scoped handoff for new Codex chats. Verify drift-prone Git,
CI, runtime, DNS, cloud, and benchmark state live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Current integration state

- Protected `origin/main` and the protected local `main` checkout agree at
  `142e62c7bbf2aa261dd701892bdf1081e39ab591`.
- The open pull-request queue is empty. The latest Live task is the Cursor MCP
  attribution smoke checklist; exact-main CI and the three CodeQL analyzers
  passed.
- Open Dependabot, code-scanning, secret-scanning, and draft security-advisory
  counts are zero. Release and source version remain 0.6.0.
- Stable `:4765` and contributor `:4766` are ready with usable model auth.
  Their source marker is the prior runtime-bearing main revision; the two
  later main commits are documentation-only, so no runtime restart is needed.
- Remote MCP is Live at 100% on ready `us-central1` revision
  `unigrok-remote-mcp-519bbdd`, image digest
  `sha256:36ff1e1d81b4454ddaf421ff99e84a1b343ba9e54c1709b30ce12bd22a9aa396`.
  Public health, readiness, OAuth protected-resource metadata, and protected
  MCP rejection probes pass.
- Control Center is Live at 100% on ready `us-central1` revision
  `unigrok-control-center-617e8cf`, image digest
  `sha256:93b0191372cf45e104a08d3294735a9c240d3ed02a73bd8c64c9bda8598cd011`.
  The homepage and `GET /api/public/v1/project` pass.
- East-region rollback services remain healthy and are not the active global
  route.

## Scratchpad safety

- Codex removed detached worktree `7812` only after proving it unloaded,
  process-free, clean, and zero commits ahead of main.
- Preserve the current detached coordinator worktree and the locked protected
  `main` checkout.
- Preserve the sponsor's primary Documents checkout of this repo: it has
  active processes and uncommitted contributor work.
- Provider IDEs may automatically rehydrate clean scratchpads. Before removing
  any candidate, re-check task state, process ownership, dirty state, unique
  commits, and open task packets. Never rely on this handoff as a live lock.

## Product boundary

- Coordinator work is limited to integration health, safe repairs, deployment
  verification, and proven-orphan cleanup. Do not invent product priorities.
- Stage 2 remains fail-closed. A new bounded live Stage 1 manifest must specify
  provider, call, cost, time, privacy, retry, and artifact limits and receive
  Codex approval at its exact head before live generation.
- Any generated dataset needs mechanical gates and another exact-head Codex
  review before training. Training and sealed evaluation require separate
  authorization.
- Publishing a new release, accepting the `google-genai` 2.x migration, or
  changing the enabled empty GitHub Wiki remains a maintainer decision.
