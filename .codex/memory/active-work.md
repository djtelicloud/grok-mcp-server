# Codex Active Work

Last updated: 2026-07-13
Owner: Codex
Status: PR #58 draft published; exact-head review and merge remain pending

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, cloud, and benchmark state live before acting. Never
record credentials, OAuth codes, tokens, or private keys here.

## Active design PR

- Draft PR #58 (`codex/authority-inversion-design`) defines the accepted target
  authority inversion. The approved runtime-distribution amendment is commit
  `0776ade47e4763ec7ad9cac5aff86b51bbf26dcb`.
- The durable public target is a supervised native `uv` Python core. HTMX and
  WebMCP are presentation/context layers; optional `app.wasm` is a browser
  Needle executor; Mojo is measured native acceleration; Docker and Docker
  Agent are optional capabilities. Gordon remains an external handoff until a
  supported machine contract exists.
- This remains a docs-only target. Current Docker-facing runtime, onboarding,
  auth, status/restart controls, and public documentation remain operational
  truth until native lifecycle and parity gates land.
- Exact amended wording passed `git diff --check` and the full suite with
  `1220 passed`. Independent review found no remaining distribution-design
  contradiction after the Gordon and browser/core boundaries were corrected.
- The updated branch is published at PR #58. Verify its current head and checks
  live because this handoff is a locator, not proof. Remaining gates are the
  normal exact-head Codex disposition, ready-for-review transition, landing,
  and protected merge. Do not begin runtime removal from this handoff.

## Completed state

- PR #56 merged to protected `origin/main` as
  `228c0c37db0211f245e92a0943823c2febd121bf`. Its exact reviewed and locally
  landed head was `b54b8d21fc9d3af78aa9465a2fd3014f6e822a2e`.
- GitHub Copilot and VS Code now share the supported repository skill at
  `.github/skills/using-unigrok/SKILL.md`. The unsupported repository-root
  `.copilot/skills` duplicate was removed during integration review.
- Click is locked at 8.4.2, clearing `PYSEC-2026-2132`, with release-hygiene
  coverage for project-skill discovery and the public UniGrok endpoint.
- PR #53 merged to protected `origin/main` as
  `381a9fe0c5909cce0ec0565bbb91d190c0543f7f`. Its exact reviewed,
  locally landed, and CI-green head was
  `64d3aef4eb500cec2ecf81722ca5dd40f0f942aa`.
- The eval harness now has a cassette-backed multi-file agent task, safe
  structural tool-trace grading, explicit task-plane selection, and a
  fail-closed `--require-pass` command-line gate.
- Swarm has two versioned golden-target manifests and an opt-in sequential
  live sweep. CLI generation is same-plane, tool-free, project/config-free,
  exact-zero metered, cancellation-safe, and retains the CLI's durable OAuth
  refresh path.
- `docs/cursorbench-readiness.md` records the evidence boundary: these checks
  are CursorBench-aligned internal capability/regression evidence, not an
  official CursorBench score.

## Verification

- The PR #56 landing gate printed
  `LANDED TO MAIN: b54b8d21fc9d3af78aa9465a2fd3014f6e822a2e`
  after 1,220 tests; the dependency audit reported no known vulnerabilities.
- Fresh PR #56 Python 3.11/3.12 CI, Project Site, control image, Docker,
  offline evals, CodeQL, and exact-head `Codex Approval` passed. Local `main`
  and `origin/main` agreed at the protected squash merge commit.
- The landing gate printed
  `LANDED TO MAIN: 64d3aef4eb500cec2ecf81722ca5dd40f0f942aa`
  after 1,219 tests and a successful contributor-runtime restart/smoke test.
- The offline eval baseline passed 13/13 with no regressions.
- The final live Docker sweep completed both targets at exact `$0.0000`
  metered cost: `nsquared_dedup` persisted 24 candidates with a 97.66% best
  latency improvement; `slow_loop_optimize` persisted 5 candidates with a
  99.82% best latency improvement.
- Grok 4.5 API reasoning and the final human exact-diff review both found no
  release blockers. Python 3.11/3.12 CI, Project Site, control image, Docker,
  offline evals, CodeQL, and exact-head `Codex Approval` passed.
- Protected `origin/main` and visible local `main` agreed at the PR merge
  commit. Stable port 4765 and contributor port 4766 were both healthy.

## Remaining external gate

- CursorBench 3.2 uses Cursor's private/internal task suite and has no public
  submission harness. An official leaderboard result or `#1` claim requires
  Cursor to evaluate UniGrok or provide an accepted interface.
- Until that happens, public claims must distinguish the reproducible internal
  suite and live golden-target results from an official CursorBench ranking.
- Broader held-out task coverage, repeated comparative runs, and confidence
  intervals remain product work for a stronger internal proxy; they are not
  retroactive evidence for the official leaderboard.
