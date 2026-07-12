# Codex Active Work

Last updated: 2026-07-12
Owner: Codex
Status: IntelligenceCapsule v1 landed; maintainer queue clean

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, and cloud identifiers live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Completed repository state

- PR #39 merged to protected `origin/main` as
  `b52ed0f187a1969f522ce4bac75d6168afc75d7d`. The exact reviewed and locally
  landed head was `5dbcac5078bdcfc75dba01f65e214baa17ff5848`.
- IntelligenceCapsule v1 now has strict Python and TypeScript canonical byte
  implementations, a shared cross-language golden vector, a published OKF
  schema, and original-byte integrity verification.
- The schema SHA-256 is pinned to
  `10c2ec4638bd6c4e303b3e2c4c7d91ae582554f48aaa01fac2d9370062b98d4c`.
  The deterministic SHA-1-format Git genesis is pinned to
  `6dadda28ac4174bf227f36b45917e15c663987ce`.
- The local-only bootstrap creates or repairs the five fixed
  `refs/unigrok/*` heads transactionally. It reads the pinned schema from the
  last-fetched public `origin/main`, rejects symbolic refs and invalid
  ancestry, disables ref dereferencing and hooks, and never fetches or pushes.
- Public MCP consumer SQLite remains unchanged and outside the Insider DAG.
  Capsule code and bootstrap do not read, migrate, copy, export, or synchronize
  `grok_sessions.db`.
- The landing gate passed 1,118 tests. Python 3.11/3.12 CI, package checks,
  CodeQL, Docker health, offline evals, Project Site, and the standalone control
  image all passed on the exact reviewed head.
- Two independent adversarial audits found no remaining capsule-protocol or
  bootstrap security/concurrency blocker. Gemini returned no actionable review
  thread. The repository's exact-head `Codex Approval` status passed.

## Runtime and Git state

- The feature content entered protected `main` at
  `b52ed0f187a1969f522ce4bac75d6168afc75d7d`; the handoff update itself lands
  in a later descendant merge, so do not treat that feature SHA as current
  `main`.
- `scripts/land-status` is the canonical live check for visible `main` and the
  contributor runtime marker. Both matched after closeout, and each closeout
  merge tree was byte-identical to its certified task head.
- Stable `127.0.0.1:4765` and contributor `127.0.0.1:4766` health checks pass.
- No real `refs/unigrok/*` were created before the protocol landed. Bootstrap
  remains an explicit local contributor action after pulling public main.

## Trust boundary

- Bootstrap `ready` means structural validity only. It does not authenticate,
  evaluate, or promote descendant intelligence.
- The envelope `signatures` array is structural and reserved in v1. It must not
  authorize work. Verified signed Git descendants or approved cloud
  attestations remain the publication-authentication boundary.
- Git is shared Insider truth. Any future Insider SQLite is only a disposable
  materialized view. Public consumer SQLite remains private runtime truth.
- No tunnel, cloud callback into localhost, shared database, or browser-held
  xAI credential is part of this architecture.

## Deliberately separate next phases

- Define protected remote projection, quarantine fetch, signature verification,
  and promotion policy for descendant intelligence.
- Add capsule object storage and signed local/cloud publishers that emit the
  same bytes without cross-calling local and cloud executors.
- Add the disposable Insider materializer and prove delete-and-rebuild recovery
  solely from trusted refs.
- Build the single adaptive contributor/admin UI on top of those contracts;
  keep public visitors read-only and public MCP consumers headless in their IDE.

No release, production deployment, or remaining gate is attached to
IntelligenceCapsule v1 itself.
