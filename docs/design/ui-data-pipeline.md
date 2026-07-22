# UI Data Pipeline — Control Center (design of record)

Owner: Claude lane. This lane owns the pipeline through the Docker gate. Codex
handoff for public release happens only after the sponsor approves the UI work.

Reference build: `unigrok-ui-v1.1.0-r9` (the build pin shown in the Runtime card).

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
space `4769` — and the active tier is derived from the port. `?preview=sky|space`
lets one running container show a higher tier's panels for review.

**Hierarchical scoping:** each tier renders its own panels plus every lower
tier's; panels above the active tier are `display:none`. Public visitors see only
public panels. Higher tiers still enforce their own auth on arrival — the nav is
navigation, not access.

| Tier | Port | Panels |
| --- | --- | --- |
| `@grok` Public Core | 4765 | metric tiles, routing planes, plane/kind/route/model/caller/fallback group-bys, runtime, tool surface, receipts, breakers |
| `@skygrok` Sky Observer | 4768 | 4-lane swarm grid, breakers + trip rates, P95 latency |
| `@spacegrok` Space Awareness | 4769 | claim-plane by-state + proof matrix, memory/RAG, SPACE=DARK security monitor |

Tier data on sky/space is **sample**, badged, and wires to live data only on the
private-overlay surfaces — never on the public core (`AGENTS.md` boundary).

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

## Phases

- **P0 — Truth** ✓ readyz-driven pills + regression test.
- **P1 — Data** ✓ `kinds` aggregate + `/runtimez` tool registry echo.
- **P2 — Panes** ✓ routing planes, tools, tiers, receipt columns.
- **P3 — Group-by + color** ✓ chart-card-first, severity standouts, level→color
  palette, per-panel coding, metric thresholds, legend.
- **P4 — Handoff (gated)** — after sponsor approval: Codex package for public
  release. Not before.

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
