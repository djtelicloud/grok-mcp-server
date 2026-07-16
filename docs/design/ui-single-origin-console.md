# Single-origin UniGrok Console — target architecture and sponsor decisions

- **Status:** Draft — awaiting Codex landing
- **Date:** 2026-07-16
- **Sponsor / provenance:** Approved by sponsor David 2026-07-16 (via Grok
  peer review). Codex owns land/deploy gates.
- **Decision owner:** David (human sponsor)
- **North star:** One UI, served same-origin by the gateway it talks to, in
  both deployments. The disjointedness of today's UI surfaces is an **origin
  problem**, not a framework problem.

## Problem

The operator/contributor UI today is split across origins: the bundled
Control Center at `localhost:4765/ui`, the hosted Control dashboard at
`control.grokmcp.org`, the public shell at `grokmcp.org`, and the
local-contributor Forge on `:4766`. Each origin carries its own auth story,
its own state glue, and its own partial view of the same product. Users cross
origins to complete one task, and the hosted twin has no first-class console
at all.

## Decision (approved architecture)

The gateway serves the **one** UI same-origin in both deployments, from the
same bundle in the same Docker image.

```text
localhost:4765/ui                     mcp.grokmcp.org/ui
  local operator console                same bundle, same image
  loopback trust, no login              public static shell
  full unredacted view, incl.           GitHub PKCE sign-in
  XAI_MANAGEMENT_API_KEY panes          every signed-in user = bound principal
  (team billing, collections mirror)    owner XAI_API_KEY pays by default (#299)
                                        business-key panes: visible-but-locked
        └────────────── one mcp_ui bundle ──────────────┘

control.grokmcp.org  → headless (OAuth AS + receipts/review broker;
                       dashboard becomes a redirect)
grokmcp.org          → stays the public docs shell
forge :4766          → stays local-contributor-only; visible-but-locked
                       in the unified nav
```

No local-to-web tunnels, ever.

### URL model

| Origin | Role after this plan |
| --- | --- |
| `http://localhost:4765/ui` | Local operator console. Loopback trust, no login. Full unredacted view including `XAI_MANAGEMENT_API_KEY`-backed panes (team billing, collections mirror). |
| `https://mcp.grokmcp.org/ui` | Hosted console. Same bundle from the same Docker image. Public static shell; sign-in unlocks per-principal surfaces. |
| `https://control.grokmcp.org` | Goes **headless**: OAuth authorization server plus receipts/review broker only. Its dashboard becomes a redirect to the Console. |
| `https://grokmcp.org` | Unchanged public docs shell. |
| `http://localhost:4766` (Forge) | Unchanged local-contributor-only surface; appears in the unified nav as visible-but-locked when not reachable. |

### Auth model

- **Local:** loopback trust, no login. This is the existing law in
  `src/http_server.py`: `_LOCAL_OPERATOR_PREFIXES` (`/ui`, `/docs`) are exempt
  from bearer auth only for verified loopback requests
  (`_is_verified_local_request` — loopback Host **and** loopback peer, or the
  explicit trusted Docker proxy declaration), enforced by
  `GatewayAuthMiddleware`. Never exempt on Cloud Run or via a non-loopback
  Host.
- **Hosted:** GitHub PKCE sign-in. Every signed-in user is a **bound
  principal** — the isolation semantics are the bound-principal wave (#264
  composed-caller attribution, #268 principal-scoped session listings, #276
  principal-scoped swarm access, #280 stateful-response denial, #292
  operator-metrics redaction). This wave is an **in-flight contract this
  console builds ON**: all five PRs are OPEN / awaiting Codex landing and
  their code is not yet on `main`, so the hosted per-principal surfaces cannot
  ship until it lands.
- **Spend:** once PR #299 (owner-default keys; supersedes #298) lands, the
  owner `XAI_API_KEY` will pay by default, with
  `UNIGROK_PRINCIPAL_XAI_KEYS_JSON` overriding for listed contributors. #299
  is OPEN / awaiting Codex landing — a Phase 0 precondition, not a shipped
  behavior.
- **Business-key panes** (`XAI_MANAGEMENT_API_KEY`-backed) render
  visible-but-locked on the hosted console; unredacted only at loopback.
- **Token exchange** for hosted sign-in goes through a **same-origin broker
  route on the gateway** — see adversarial finding 1 below for why the
  browser cannot complete the token exchange against the AS directly.

## Sponsor decisions (David via Grok peer review, 2026-07-16)

1. **Owner-key default spend:** write+ GitHub collaborators only
   (write/maintain/admin). Read-only collaborators and outsiders: no.
   **Enforcement gap (from the #299 review, 2026-07-16):** PR #299's
   `resolve_xai_api_key` grants the owner key to *every* authenticated OAuth
   principal — it holds no permission level, so the write+ boundary is not
   enforced in this repo. It is delegated to the external
   control.grokmcp.org introspection gate via the `unigrok:invoke` scope,
   which is currently unverified. Phase 0 precondition: prove that gate
   rejects read-level principals before the Console relies on this decision.
   The Console's spend-attribution panel shows `source: owner_default |
   principal` truthfully and must **not** imply a permission-level guarantee
   this layer does not make.
2. **Token lifetime:** add a refresh grant to the OAuth AS (better hosted
   UX). Until it ships, honest full-page re-login every 10 minutes is
   acceptable — never silent-iframe tricks.
3. **SIWC / Sites control rollback surface:** retire, but only **after** the
   hosted Console + OAuth path is proven Live.
4. **Control-center PR-evidence panels** (PR table, review score ring,
   checks): move into the Console behind a same-origin gateway proxy; do not
   keep a forever Control mini-app.

## Stack decision

**No framework migration in Phase 1.** The disjointedness is an origin
problem, not a framework problem. Revisit Alpine.js (CSP build) or htmx
(`allowEval=false`) **only after** the Console is one origin, and only if
hand-rolled state/UI glue still hurts. This is a deferral, not a permanent
ban. Note the gateway CSP already pins `script-src 'self'`
(`src/http_server.py`), so any future framework must pass that constraint
unmodified.

## Grounding (verified before this decision)

| Evidence | Why it matters |
| --- | --- |
| PR #299 — owner-default plus optional teammate xAI keys (supersedes #298); **OPEN / in flight** | Defines who pays on the hosted console; Phase 0 landing precondition, not yet on `main`. |
| Bound-principal wave #264/#268/#276/#280/#292 — **all OPEN / awaiting Codex landing** | The in-flight contract the hosted console builds ON: once the wave lands, signed-in users get the per-principal isolation semantics truthful surfaces need. Not yet on `main`. |
| `src/http_server.py`: `_LOCAL_OPERATOR_PREFIXES` loopback law | The local no-login trust boundary already exists and is Host+peer verified — the local console needs no new auth. |
| `src/http_server.py`: `GatewayAuthMiddleware` | Bearer/OAuth-introspection enforcement point that the hosted `/ui` static shell stays outside of and per-principal API calls stay inside of. |
| `src/http_server.py`: `MCPOriginMiddleware` | DNS-rebinding/origin guard on `/mcp` and `/v1`; the same-origin console passes as loopback or allowlisted origin without weakening it. |
| `src/http_server.py`: CSP `script-src 'self'` | One UI must be self-hosted assets only; also constrains any future framework choice. |
| `mcp_ui/` ships in the wheel (`pyproject.toml` package map) and the Docker image (`Dockerfile` `COPY mcp_ui/`) | The "same bundle from the same image" property already holds; hosting it is a serve-and-gate problem, not a packaging problem. |

## Adversarial findings (and their design consequences)

1. **AS token-exchange endpoint is not browser-callable from the gateway
   origin.** The OAuth AS at `control.grokmcp.org` *does* send
   `access-control-allow-origin: *` on some well-knowns
   (`/.well-known/oauth-authorization-server`,
   `/.well-known/oauth-protected-resource`) and on `/oauth/register`, so this
   is **not** a blanket "no CORS anywhere." But the browser **token exchange**
   endpoint (`/oauth/token`) is not CORS-enabled for the `mcp.grokmcp.org`
   origin, **and** the gateway CSP declares no `connect-src` for
   `control.grokmcp.org` (its `connect-src` falls back to the strict
   `default-src 'self' …`, verified in `src/http_server.py`) — so a browser
   page on `mcp.grokmcp.org/ui` cannot call the AS token endpoint directly.
   **Consequence:** token exchange goes through a **same-origin broker route
   on the gateway** (the clean path regardless), which performs the exchange
   server-side.
2. **10-minute token TTL with no refresh grant.** The AS currently issues
   short-lived tokens and offers no refresh grant. **Consequence:** until the
   refresh grant ships (sponsor decision 2), the console does an **honest
   full-page redirect to re-login** when the token expires — no silent-iframe
   renewal, no fake session continuity.

## Phases

| Phase | Work | Gate |
| --- | --- | --- |
| 0 | Land PR #299 (owner-default keys, currently OPEN); #298 closes as superseded | Codex lands #299 — precondition for every later phase |
| 1 | Serve the existing `mcp_ui` bundle as a public static shell on the Cloud Run twin (`mcp.grokmcp.org/ui`) — no framework migration | Shell loads hosted, signed-out, without weakening `GatewayAuthMiddleware` |
| 2 | GitHub PKCE sign-in plus the same-origin token broker route on the gateway | Browser sign-in completes without any direct browser→AS call |
| 3 | Per-principal truthful surfaces: signed-in users see their own sessions/spend; business-key panes visible-but-locked | **Blocked until the bound-principal wave (#264/#268/#276/#280/#292) lands on `main`;** then its per-principal semantics are verified in the UI |
| 4 | Unified nav across local console, hosted console, Forge (visible-but-locked when unreachable) | One nav, no tunnels |
| 5 | Shrink control-center: move PR-evidence panels behind the gateway proxy; `control.grokmcp.org` dashboard becomes a redirect (headless AS + broker remains) | No forever Control mini-app |
| 6 | Deploy probes for the hosted console (health/readiness/auth-path checks) | Hosted Console proven Live; only then retire SIWC/Sites rollback surface (sponsor decision 3) |

## Explicit NO-GOs

- Local-to-web tunnels of any kind.
- Silent-iframe token renewal or any dishonest session-continuity trick.
- Editing `docs/remote-mcp-deployment.md` in this decision record's PR
  (#298/#299 rewrite it; zero conflict surface by design).
- Owner-key spend for read-only collaborators or outsiders.
- Weakening `_LOCAL_OPERATOR_PREFIXES`, `GatewayAuthMiddleware`,
  `MCPOriginMiddleware`, or the `script-src 'self'` CSP to make the hosted
  console easier to ship.

## Related

- [remote-mcp-deployment.md](../remote-mcp-deployment.md) (being rewritten by #298/#299 — not edited here)
- [ADR 0001](../adr/0001-cloud-control-plane-governance.md)
- [public-vs-insider-surfaces.md](public-vs-insider-surfaces.md)
- [hosted-review-p0.md](hosted-review-p0.md)
