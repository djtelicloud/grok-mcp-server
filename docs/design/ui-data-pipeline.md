# UI Data Pipeline — Control Center (design of record)

Owner: Claude lane. This lane owns the pipeline through the Docker gate. Codex
handoff for public release happens only after the sponsor approves the UI work.

Reference build: `unigrok-ui-v1.1.0-r13` (the build pin shown in the Runtime card).

## Purpose

One honest, self-contained Control Center at `/ui/`, fed only by the gateway's
own telemetry truth — no prompt capture, no identity, no external assets. The
page never claims a state the runtime cannot prove, and every panel's color
carries meaning.

## Architecture

```
telemetry writes                read path                     render
────────────────                ─────────                     ──────
gateway tool calls ──► PublicStateStore.save_telemetry
                          │  (SQLite: telemetry table)
                          ▼
                       telemetry_summary(limit=1000)
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
   GET /readyz       GET /benchmarkz    GET /runtimez
   (liveness truth)  (summary +         (mode, backend, tool
                      breakers)          registry, version)
        └─────────────────┴──────────────────┘
                          ▼
              static/dashboard.html  (single inline script,
              per-response CSP nonce, 10 s poll)
```

The dashboard is baked into the image (`src/unigrok_public/static/`); compose
has no source bind mount, so the Docker gate — rebuild, restart, probe — is the
only path to seeing a change live. `_ui_index_response` injects a per-response
nonce into the **first** inline `<script>` and pins CSP to it, so the page keeps
exactly one inline script.

## Three trust tiers, one page

The same baked page serves all three surfaces. A unified switcher (`#tiernav`)
links each tier to its own port on the current host — public `4765`, sky `4768`,
space `4769` — and the active tier is derived from the port. Every tier click is
native full navigation; the destination page reads only its own same-origin
feeds. Forge is not a data proxy and never relabels its own feeds as Sky or
Space.

The top-tab labels stay deliberately terse: `@grok`, `@skygrok`, and
`@spacegrok`. The destination page title and eyebrow carry the longer
GroundCommand/SkyCommand/SpaceCommand description.

**Hierarchical scoping:** each tier renders its own panels plus every lower
tier's; panels above the active tier are `display:none`. Public visitors see only
public panels. Higher tiers still enforce their own auth on arrival — the nav is
navigation, not access. The Space tab stays visible as an access-dependent
upgrade advertisement. If its loopback port is absent, navigation fails
honestly; there is no Sky fallback or sample-as-live replacement.

| Tier | Port | Panels |
| --- | --- | --- |
| `@grok` Public Core | 4765 | metric tiles, routing planes (+per-plane usage), plane/kind/route/model/caller/fallback group-bys, runtime, build & durable work, policy & governance, connect-an-IDE, tool surface, receipts, breakers |
| `@skygrok` Sky Observer | 4768 | 4-lane swarm grid, breakers + trip rates, P95 latency, GitHub reviews (Grok-score ring + PR standouts), live-run streaming |
| `@spacegrok` Space Awareness | 4769 | claim-plane by-state + proof matrix, memory/RAG, SPACE=DARK security monitor, sealed report card, linked devices |

Shared runtime fields on sky/space come from that selected origin. Unfinished
private-overlay panels remain **sample** and badged; they are never hydrated
from a lower tier (`AGENTS.md` boundary).

## Visual system

**Group-by first.** Every section leads with a chart card (the `bars`/`cbars`
group-by), like the top of the board. Only genuine **standouts** drop to a
compact table; the full list is tucked behind a `<details>`:

- Tool surface → billing-class bar chart → destructive-tools standout table →
  full 29-tool registry collapsed.
- Receipts → outcome bar chart (pass/fail/unverified) → severity-ranked
  "needing attention" table → full log collapsed.
- Space claim plane → claims-by-state bars → proof matrix.

**Standouts are computed, not eyeballed.** Per-receipt severity score:
`fail 100 + fallback 40 + (slow vs p95) up to 40 + (cost) up to 30`, sorted
descending, top 8. Tools rank by exposure (destructive → metered → api_account).

**Level → color palette** (the canvas map, dark hexes; light values recorded in
a CSS comment for a future light board). A `levelOf()` classifier maps each
status string to a level so color follows meaning:

| Level | Hex | Applies to (keywords) |
| --- | --- | --- |
| great | `#3FA266` | Live, READY (true claim), PROMOTE YES, ready, active |
| good | `#81A1C1` | DONE, sealed, accepted, claim-plane |
| expected | `#7BAFE9` | offline / not_ready, idle, OPEN, ABSTAIN, SPACE=DARK, DIFF_OFF, gate_id null, zero-write |
| warning | `#F1B467` | fallback, slow, stale, drift, degraded |
| threat | `#DD7F76` | BLOCKED, busy, fail, breaker OPEN |
| critical | `#FC6B83` | breach, secret, dirty (reserved) |

A legend strip decodes these six levels. Interpretation note: in a
no-credential runtime, `not_ready`/`offline` is a *known* READY-false state, so
it reads **expected** blue, not threat orange.

**Per-panel color coding.** Color encodes a level or a real cross-panel category;
dimensions with no inherent severity stay neutral (single gradient) rather than
decorative:

| Panel | Coding |
| --- | --- |
| Verified success, P95 latency | threshold → great / warning / threat |
| Plane mix | api blue · cli green · local blue (matches routing-planes card) |
| Request kinds | metered → warning amber · free → neutral |
| Route mix | error → threat · else neutral |
| Model mix, Top callers | neutral (no severity) |
| Fallback categories | warning amber (degraded events) |
| Receipt outcomes | pass great · fail threat · unverified neutral |
| Tool billing | metered amber · api_account blue · conditional cyan · non_metered neutral |
| Breakers | closed expected · OPEN threat |
| Claims / SPACE monitor | per `levelOf()` (READY great, ABSTAIN/DIFF_OFF/null expected) |

**Styling tokens** stay pixel-faithful to the shipped 4765 gold system (the file
wins over any doc): page `#080b14` + nebula, card gradient `145deg #141c33e8 →
#0d1222e8`, hairline `#263252`, cyan `#58e6d9`, blue `#58a6ff`, purple `#bc8cff`,
18 px card radius, 99 px pills, Inter throughout. New panels reuse the `card` /
`bars` / `pill` primitives.

## Data-flow rules (honesty)

1. Service pill renders `readyz.status` verbatim; never hard-coded.
2. Aggregates come from `telemetry_summary` buckets; the page computes only bar
   widths and the severity ranking.
3. Receipts render verbatim fields, including `created_at` (UTC), `request_kind`,
   `stop_reason`.
4. Empty states are honest ("No samples yet.", "No provider calls recorded.");
   seeding is never required for correctness.
5. No sealed READY and no non-null `gate_id` is ever invented; `gate_id null` and
   `ABSTAIN OPEN` are preserved on the Space tier.

## Data → access matrix (three tiers)

Knowledge and access **increase upward**: Ground ⊂ Sky ⊂ Space. Every data
feature has a **floor tier** — the lowest tier allowed to receive it. It is
visible at its floor and every tier above, and structurally unreachable below.
The top-nav click is the access-escalation act: it is a full navigation into
the higher tier's own origin and auth, never a data fetch.

Four structural guards enforce "never below":

1. **Browser boundary** — CSP `connect-src 'self'`: a page served by one tier
   cannot fetch another tier's endpoints. Cross-tier data on a lower surface is
   impossible in the browser, not merely avoided.
2. **Server projection** — secrets become booleans (`api.configured`), names
   become existence bits (`layer_collection`, `collection_label_set`), internal
   URLs stay server-side (`_probe_local` returns ready/models, never the
   runtime URL), caller labels are sanitized `[a-z0-9._:-]` and capped at 80.
3. **Mode gate** — `is_cloudrun_runtime()` collapses `/readyz` to `{status}`:
   the hosted public surface is the most-restricted projection of all.
4. **Tenant gate** — `telemetry_summary(caller=_tenant_caller())`: an
   authenticated principal sees its own slice; anonymous local sees only the
   own-store aggregate.

Downward-leak classes: **S** secret · **N** name/identity · **T** topology ·
**D** higher-tier data · **$** spend.

### Floor L0 — Ground (`@grok` public core, 4765)

| Data feature | Source (env → server) | Wire field | Access gate | UI panel | Leak class → guard |
| --- | --- | --- | --- | --- | --- |
| Service liveness | server state | `readyz.status` verbatim | none | service pill | — |
| Tier identity | `UNIGROK_LAYER` | `healthz.layer`, `runtimez.layer` | per-instance env | title/eyebrow (r19: authoritative over port-sniff) | N → name only |
| Layer collection exists | `UNIGROK_LAYER_COLLECTION` | `runtimez.layer_collection` **bool** | existence-only | runtime card | N → bool projection, never the name |
| CLI plane | `grok login` volume (`UNIGROK_AUTH_PATH`) | `planes.cli.{ready,models,default_model,billing,transport}`, `credential_planes.cli` | cloud mode disables | routing planes | S → auth file never on wire |
| API plane | `XAI_API_KEY` + aliases (`_SKY_INFERENCE`, `_GROUND`, `_UNIGROK_GROUND`) | `planes.api.{configured,ready}`, `credential_planes.api.{spend_enabled,can_spend,models,image_models}` | `UNIGROK_ENABLE_METERED_API` | routing planes + governance | S → alias resolved server-side, only booleans cross; management/Cursor tokens in forbidden set |
| Local plane | `UNIGROK_LOCAL_AUTO`, `UNIGROK_LOCAL_RUNTIME_URL` | `planes.local.{configured,ready,runtime_up,models,default_model,data_ready}` | probe fail-closed | routing planes (r19: real DMR state) | T → runtime URL never in payload |
| Plane policy | mode | `credential_planes.{policy,preferred_plane,effective_plane,degraded,service_usable}` | mode | routing card header (r19) | — |
| Plane notices | policy eval | `credential_planes.notices[{id,severity,summary,action}]` | fixed template strings | governance (r19) | S-safe → static text, never interpolates key/alias values |
| Telemetry aggregates | state store | `benchmarkz.telemetry.{sample_size,verified_success_rate,latency_ms,cost_usd,callers,models,routes,planes,kinds,fallbacks}` | tenant gate | dials + group-bys | $ → own-store / own-tenant only |
| Receipts | telemetry rows | `recent[{caller,model,route,resolved_plane,request_kind,fallback_reason,latency_ms,cost_usd,created_at,stop_reason,success}]` | tenant gate | severity standouts + collapsed log | N → caller sanitized+capped; prompt text never stored |
| Circuit breakers | `UNIGROK_BREAKER_*` | `circuit_breakers` snapshot | — | breaker strip | — |
| Build/ACP | runtime | `runtimez.grok_build` | — | build & durable work | — |
| Governance | `UNIGROK_AUTONOMY`, `UNIGROK_MISSION_V2`, `UNIGROK_TASK_CLASS`, `UNIGROK_VERIFY_LITERAL`, `UNIGROK_CONTEXT_PACK`, `UNIGROK_ENABLE_METERED_API` | `runtimez.{autonomy,api_spend_enforcement,routing_advisor,semantic_evaluation,needle_active}` | — | policy card (spend-enabling reads warning amber) | $ |
| Request limits | `UNIGROK_*_TIMEOUT`, caps | `runtimez.request_limits.*` | — | runtime small print | — |
| Tool registry | code | `runtimez.tools[]`, `tool_count` | — | tool surface chart | — |
| Memory / RAG | task-rag env + facts table | `task_rag.{configured,mode,chat_memory,collection_label_set}` + r19 `fact_count` | existence-only for collection label | memory panel (r19) | N → label presence bool, never name |
| Connect config | `_configured_mcp_url()` | url + `X-Client-ID` | deliberate publication | connect card | non-secret by design |
| Tier nav | `UNIGROK_PUBLIC_PORT` / `UNIGROK_SKY_PORT` / `UNIGROK_SPACE_PORT` plus optional per-tier URLs (names-only) | r19 `runtimez.tier_nav` | **suppressed in cloud mode** | top nav | T → loopback-bound ports; hosted surface omits the block |

### Floor L1 — Sky (`@skygrok`, 4768) adds

| Data feature | Source | Floor guard on lower tiers | UI panel |
| --- | --- | --- | --- |
| Swarm 4-lane grid, trip rates | private overlay | not rendered on L0; the visible Sky tab navigates to the Sky origin | swarm grid |
| GitHub review ring + PR standouts | private overlay | same | reviews |
| Live-run SSE stream | private overlay | same; Run trigger stays disabled until contributor surface (spend-capable POST is an operate feature, never faked) | live run |
| Team assignment board | private overlay (Sky env home) | same | team board |

**Access features added at L1:** contributor sign-in (OAuth on
control.grokmcp.org, never local), review ops, run trigger, team assignment.

### Floor L2 — Space (`@spacegrok`, 4769) adds

| Data feature | Source | Floor guard on lower tiers | UI panel |
| --- | --- | --- | --- |
| Claim plane by-state + proof matrix | private overlay | sample shell only below; `gate_id null` / `ABSTAIN OPEN` preserved verbatim, never invented READY | claims |
| Sealed report card (Wilson floors) | private overlay | same | report card |
| SPACE=DARK security monitor | private overlay | same | monitor |
| Linked devices | control plane | same; mint/revoke live only on control plane | devices |

**Access features added at L2:** device enrollment (one-time codes),
sealed-report publication.

`SPACE=DARK` still means zero-write awareness, no Ground-to-Space MCP wiring,
and no public model promotion. The visible Space navigation tab is the narrow
exception: it advertises the higher tier and relies on port reachability plus
the destination's own controls; it does not grant Space authority.

### Credential inheritance — upward trickle

Auths and keys follow the same lattice as data: **inherited upward, never
downward.** Ground's credentials are the floor every higher tier receives;
each tier may add or override with its own; nothing defined at a higher tier
ever reaches a lower tier's env, volumes, or wire.

Mechanics (already supported, no code needed):

- **Env layering** — compose `--env-file` stacks left-to-right, later wins:
  Ground `--env-file ground.env`; Sky adds `--env-file sky.env`; Space adds
  `--env-file space.env`. A tier's launch command names only its own file and
  its ancestors' — never a descendant's.
- **Key resolution** — the inference allowlist order (`XAI_API_KEY`,
  `_SKY_INFERENCE`, `_GROUND`, `_UNIGROK_GROUND`) already encodes the trickle:
  an explicit key wins, then the most tier-specific available, with Ground as
  the floor. Management/Cursor tokens stay in the forbidden set at every tier.
- **CLI auth** — the `grok login` volume is Ground-owned and may be mounted
  upward by Sky/Space stacks; higher tiers may instead hold their own login,
  but a higher tier's auth volume is never mounted by a lower stack.
- **Wire guard unchanged** — whatever a tier inherits, only
  `configured`/`ready` booleans cross to its UI; inheritance changes what a
  tier *can use*, never what it *reveals*.

Forge staging inherits as Ground: the checkout `.env` (Ground home) is the
one file `--live` carries.

### Identity flow — GitHub-gated, gateway-owned

The deck never reads the browser's github.com session; identity exists only as
the gateway's own principal (GitHub is the IdP behind `/auth/github`). The
contributor endpoints (`/control`, `/auth/github`, `/api/me`, #508 forge-surface
hooks) are 404 on the public surface (identity-free contract) and 401 on the
forge until the OAuth slice answers. The deck polls `/api/me` and renders
exactly that truth:

| `/api/me` | Meaning | Deck state |
| --- | --- | --- |
| 404 / absent | public surface, no identity exists | button links out to the control site (marketing) |
| 401 | forge surface, signed out | "Continue with Cloud" reuses the existing Control OAuth registration; "Use device code" remains the explicit fallback |
| 200 `{login, tier}` | gated session | username-only pill (click = sign out); server-granted `tier` may raise the visible tier (never lower, never below the surface floor) |

**Preferred Cloud link** (`github_auth.py`, Forge only):
`/auth/control/start` dynamically registers a loopback PKCE client with the
existing `control.grokmcp.org` OAuth server, then performs a top-level
navigation. Control reuses its signed GitHub App cookie when present, otherwise
it runs the established GitHub login, rechecks write/maintain/admin repository
access, and redirects to `/auth/control/callback`. Forge exchanges the code
server-side and stores only the scoped **UniGrok** `unigrok:connect` token in a
Forge-owned `0600` file beside its persistent state database. No GitHub token
or Control cookie crosses into localhost. `/api/me` introspects the token at
most once per 60 seconds; role loss clears the local link. The link therefore
survives page, browser, process, and container restarts until explicit logout
or access revocation. On a signed-out Forge load, the deck makes one top-level
Control round trip automatically; an existing Control/GitHub session returns
straight to the deck without another click or credential prompt. A failed
Control start returns to the deck with the device-code fallback visible.
Explicit logout disables that automatic relink in the browser until the user
chooses **Continue with Cloud** again.

**Device-code fallback** (`github_auth.py`, Forge only):
`/auth/github/start` asks GitHub for a one-time code (public
`UNIGROK_GITHUB_CLIENT_ID`, no secret); the deck shows the code linking
github.com/login/device; `/auth/github/poll` exchanges on GitHub's
confirmation, reads the identity once, **discards the GitHub token**, and sets
an HttpOnly SameSite signed session cookie. Its signing key is created once in
the Forge state volume, so the 12-hour session survives process/container
restart. `UNIGROK_CONTRIBUTOR_LOGINS` allowlists who gains
`UNIGROK_CONTRIBUTOR_TIER` (default sky); everyone else signs in at tier
public. No password ever touches the deck; every failure keeps its honest name
(`github_oauth_not_configured`, `github_unreachable`, `denied`,
`flow_expired`). If Control introspection is temporarily unavailable, a valid
device session remains usable and the remembered Control token is retained.
Forge auth mutations require a non-simple same-loopback request, preventing a
foreign page from silently unlinking the machine.
Forge's canonical auth/cookie origin comes from `UNIGROK_FORGE_URL` (or the
`UNIGROK_FORGE_PORT` fallback) and is intentionally independent from
`UNIGROK_PUBLIC_URL`/`UNIGROK_PUBLIC_PORT`. The switchboard can therefore send
`@grok` to public core `:4765` without breaking Forge login on `:4766`.

Signed-out is never dressed up; the granted tier is server truth, so the
GitHub gate — not the client — controls what data shows.

### Verified downward-leak audit (against `origin/main` 664c55c)

| # | Vector | Verdict | Evidence / guard |
| --- | --- | --- | --- |
| 1 | Local runtime URL on wire | **clean** | `_probe_local` projects status fields only |
| 2 | API key alias names surfacing | **clean** | allowlist resolver; only `configured` bool crosses; forbidden env set blocks management/Cursor tokens |
| 3 | Collection names | **clean** | existence-only booleans |
| 4 | Notices embedding secrets | **clean** | static template strings; only public referral URL interpolated |
| 5 | Caller identity leakage | **clean** | middleware sanitizes `[a-z0-9._:-]`, 80-char cap; no prompt capture |
| 6 | Hosted over-exposure | **clean** | cloudrun `/readyz` → `{status}` only |
| 7 | Cross-tier fetch from lower page | **impossible** | CSP `connect-src 'self'`; nav is navigation |
| 8 | Cross-tenant telemetry | **clean** | principal-scoped summary |

Residual risks (accepted, watched): local anonymous aggregate shows all
callers on a shared box (by design, own store); r19 `tier_nav` advertises
higher-tier loopback ports (names-only env, hosted-suppressed). Sky and Space
must remain loopback-bound until those destinations enforce remote-user auth.

## Forge console fold

The legacy 4766 console's features are absorbed into this design:

- **Port-bound tier navigation**: Forge defaults its Public target to canonical
  core `4765`; Sky and Space use the authoritative `runtimez.tier_nav` targets.
  Native navigation rebinds every relative feed and MCP snippet to the selected
  origin while preserving CSP `connect-src 'self'`.
- **Live on public**: per-plane usage (calls + recorded cost on the routing
  card), connect-an-IDE (known clients cross-referenced with live caller
  telemetry, copy-to-clipboard **non-secret** MCP config — url + `X-Client-ID`
  only, endpoint from `/runtimez`), build & durable work (`grok_build` ACP
  metrics; active in-flight reads great), policy & governance (routing advisor,
  semantic-eval, API-spend, autonomy, needle, recovery — **spend-enabling
  settings read warning amber**, safe defaults expected blue).
- **Sample shells on sky**: GitHub reviews (Grok-score ring + PR standout table,
  state-colored) and the live-run streaming strip (SSE depth/tool/cost events;
  the Run trigger stays disabled until the contributor surface wires it — a
  spend-capable POST is an operate feature, never faked as telemetry).
- **Sample shells on space**: sealed report card (headline floor + Wilson
  fair-range table; small samples stay in the small print) and linked devices
  (one-time-code enrollment; mint/revoke wire to the control plane).

## Phases

- **P0 — Truth** ✓ readyz-driven pills + regression test.
- **P1 — Data** ✓ `kinds` aggregate + `/runtimez` tool registry echo.
- **P2 — Panes** ✓ routing planes, tools, tiers, receipt columns.
- **P3 — Group-by + color** ✓ chart-card-first, severity standouts, level→color
  palette, per-panel coding, metric thresholds, legend.
- **P3.5 — Forge fold** ✓ connect/usage/build/governance live; contributor
  shells (reviews, live run, report card, devices) sample-badged on sky/space.
- **P4 — Handoff (gated)** — after sponsor approval: Codex package for public
  release; private-overlay wiring for the sky/space shells lands in a local
  session against the intelligence repo. Not before.

## Forge staging gate (prod rehearsal)

Forge (4766, `--live`) is treated as production during approval: real CLI auth
volume, real state volume, real metered key via the checkout `.env` — full
situation awareness, no pretend states. Approval to core requires all of:

- [ ] Service pill `ready`; CLI **and** API tiles green with live model lists
      (local tile shows its true probe state — ready, runtime down, or off).
- [ ] Policy strip `cli_first · effective cli`, not degraded; governance shows
      only owner-intended notices (`metered_api_enabled_by_owner` is expected
      when spend is armed; anything else gets explained or fixed).
- [ ] Cost dial equals the receipts' recorded spend, split by plane; receipts
      severity standouts are live traffic, not `dev-seed:*`.
- [ ] Tier nav ports match the operator's factory layout; `layer` names the
      tier the instance actually serves.
- [ ] Stored-facts count non-zero and matching the operator's store.
- [ ] Zero page errors in the browser console over a full 10 s poll cycle,
      including one forced `/runtimez` outage (panels must degrade honestly).
- [ ] Byte parity: container `dashboard.html` sha256 equals the bundle's.

Only after this live pass does the P4 Codex handoff proceed.

## Verification gate

Every round before showing work: `uv run ruff check .`, `uv run pytest -q`,
`bash scripts/ci-insider-denylist.sh`,
`uv run python scripts/check_release_contract.py`,
`uv run python scripts/check_docs.py`, `docker compose config --quiet`, then a
Docker rebuild, a live probe of `/ui/` `/readyz` `/benchmarkz`, and a
headless-browser console check (no page errors) before the screenshot.

## Dev tooling (not shipped)

`scripts/seed_dev_telemetry.py` writes 48 clearly-labeled `dev-seed:*` receipts
into a throwaway local volume so panes render during UI work; empty-state paths
are verified before seeding. The sandbox image builds from a scratchpad
`Dockerfile.dev` (mirror base, stubbed CLI); the shipping `Dockerfile` is
untouched.
