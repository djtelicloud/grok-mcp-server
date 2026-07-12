# Codex Active Work

Last updated: 2026-07-12
Owner: Codex
Status: Swarm v2, local runtimes, and public Playground rollout complete

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, and cloud identifiers live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Completed repository state

- Protected `origin/main` and visible local `main` were synchronized after PR
  #25. Use `scripts/land-status` for the current merge commit; this tracked file
  cannot name the merge commit that contains its own update.
- The Swarm v2 implementation is integrated: bounded paste-code analysis,
  function-aware search setup, goal-specific champion selection, strict status
  v2 receipts, elite-offspring lineage, deterministic AST transforms, and the
  copy-only paste workflow all ship from protected main.
- The full landing suite passed 1,045 tests. Offline evals passed 12/12 and the
  deterministic OKF bundle was clean.

## Local runtime state

- The workspace-neutral stable service was rebuilt from protected main and is
  healthy at `http://127.0.0.1:4765`; its Swarm page auto-loads the recorded
  verified tour and routes live work to contributor Forge.
- The attached contributor service was rebuilt from protected main and is
  healthy at `http://127.0.0.1:4766`; `UNIGROK_SWARM=dry_run` remains the local
  setting, so search and scoring are enabled while Apply remains disabled.
- A real browser-launched paste run completed two elite-offspring generations
  with 100% focus coverage, a stable benchmark, 75% candidate feasibility, two
  verified Pareto elites, a 98.8% latency improvement, an explicit 838.5%
  peak-memory increase, and $0.0000 model cost.
- The guided Playground now accepts a large Python paste, computes exact local
  analytics, lets the user select a function, goal, and search strategy, runs a
  verified local search, explains code and runtime trade-offs, and copies the
  best verified code. Paste tasks remain deliberately copy-only.

## Production state

- `https://grokmcp.org` is published with Sites version 7. The public
  `https://grokmcp.org/swarm/` route serves the same Playground UI with
  browser-only analysis, an honest recorded run, and a clear path to verified
  local execution; the homepage links it directly.
- Public project JSON reports `https://mcp.grokmcp.org/mcp` as a private OAuth
  API-plane MCP.
- `https://control.grokmcp.org` serves the GitHub role-gated control plane and
  OAuth authorization server. Its RFC 8414 metadata is live.
- `https://mcp.grokmcp.org` has active Google-managed TLS. `/healthz` returns
  healthy, RFC 9728 protected-resource metadata is live, and unauthenticated
  `/mcp` returns the required OAuth bearer challenge.
- The remote MCP remains API-plane-only with live GitHub membership
  introspection, scoped short-lived tokens, per-caller budgets, Cloud Run
  ingress restricted to the load balancer, Cloud Armor attached, CDN disabled,
  and raw service URLs disabled.
- The hosted review broker remains read-only and immutable-head-bound. The
  landing receipt broker verifies completed landing evidence and signs receipts
  with Ed25519; neither broker has cloud merge or release mutation authority.

## Safety posture

- Keep the stable service workspace-neutral and keep Swarm execution limited to
  contributor mode, an attached workspace, and non-Cloud-Run runtime.
- Keep `UNIGROK_SWARM` off by default in product configuration. Local dry-run
  enables search and scoring only; active mode and Apply require an explicit
  operator decision plus post-apply verification.
- Do not expose xAI, GitHub, OAuth, receipt-signing, or tunnel credentials to a
  browser or IDE configuration.
- Preserve the public documentation plane while protected control and MCP
  routes fail closed. Do not enable Cloud CDN or raw Cloud Run URLs.

## Remaining work

No release, deployment, integration, issue-tracker, or runtime gate remains for
this rollout. Future work should start from a new scoped issue or handoff.
