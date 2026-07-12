# Codex Active Work

Last updated: 2026-07-12
Owner: Codex
Status: Cloud control, private remote MCP, and guided Swarm UI rollout complete

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, and cloud identifiers live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Completed repository state

- Protected `origin/main` and visible local `main` were synchronized after PR
  #19. Use `scripts/land-status` for the current merge commit; this tracked file
  cannot name the merge commit that contains its own update.
- Open GitHub issues and pull requests were zero at closeout. Completed agent
  branches and worktrees were removed after their patches were integrated or
  proven superseded.
- The full landing suite passed 1,027 tests. Offline evals passed 12/12 and the
  deterministic OKF bundle was clean.

## Local runtime state

- The workspace-neutral stable service was rebuilt from protected main and is
  healthy at `http://127.0.0.1:4765`; its Swarm page auto-loads the recorded
  verified tour and routes live work to contributor Forge.
- The attached contributor service was rebuilt from protected main and is
  healthy at `http://127.0.0.1:4766`; `UNIGROK_SWARM=dry_run` remains the local
  setting, so search and scoring are enabled while Apply remains disabled.
- A real browser-launched golden demo completed three generations with 100%
  focus coverage, a stable benchmark, 100% candidate feasibility, one verified
  Pareto elite, a 97.9% latency improvement, an explicit 1229.9% peak-memory
  increase, and $0.0000 model cost.
- The guided Playground opens with useful data, selects the front-ranked elite,
  explains speed/memory trade-offs, distinguishes stable from contributor mode,
  keeps manual task IDs/tokens under Advanced, and exposes keyboard-operable
  candidate receipts.

## Production state

- `https://grokmcp.org` is published with Sites version 6. Public project JSON
  now reports `https://mcp.grokmcp.org/mcp` as a private OAuth API-plane MCP.
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
