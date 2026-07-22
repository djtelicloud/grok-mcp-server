# UI Data Pipeline — Control Center plan

Owner: Claude lane. This lane owns the pipeline through the Docker gate. Codex
handoff for public release happens only after the sponsor approves the UI work.

## Purpose

One honest, self-contained Control Center page at `/ui/` on the public core
(4765), fed exclusively by the gateway's own telemetry truth. No prompt capture,
no identity, no external assets. The page must never claim a state the runtime
cannot prove.

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
   (liveness truth)  (summary +         (mode, backend,
                      breakers)          tool_count, version)
        └─────────────────┴──────────────────┘
                          ▼
              static/dashboard.html  (single inline script,
              per-response CSP nonce, 10 s poll)
```

- The dashboard is baked into the image (`src/unigrok_public/static/`); compose
  has no source bind mount, so the Docker gate — rebuild, restart, probe — is
  the only path to seeing a change live.
- `_ui_index_response` injects a per-response nonce into the **first** inline
  `<script>` tag and pins CSP to it. The page therefore keeps exactly one
  inline script.

## Components

| Component | File | Role |
| --- | --- | --- |
| Telemetry store | `state.py` (`telemetry` table) | Receipt of record: caller, kind, planes, model, latency, cost, fallback, stop reason |
| Summary aggregator | `state.py` `_telemetry_summary_sync` | Groups by caller/model/route/plane/kind/fallback; percentiles; `recent[25]` |
| Status endpoints | `server.py` `/readyz` `/benchmarkz` `/runtimez` | The only data the page may render |
| Dashboard | `static/dashboard.html` | Panes + pills + receipts table |
| Dev seed | `scripts/seed_dev_telemetry.py` | Clearly-labeled sample receipts for local UI work only |
| Regression tests | `tests/test_control_center_ui.py` | Honesty and pane-contract checks |

## Data flow rules

1. **Honest pills.** The Service pill renders `readyz.status` verbatim
   (`ready` / `not_ready`); it is never hard-coded. CLI/API pills render
   per-plane readiness from `readyz.planes`. A fetch failure renders
   `Service unavailable`, never a stale claim.
2. **Aggregates come from the store.** Plane mix, request kinds, routes,
   models, callers, fallbacks all come from `telemetry_summary` buckets; the
   page computes nothing but bar widths.
3. **Receipts are verbatim.** The table renders `recent` fields including
   `created_at` (Time, UTC), `request_kind` (Kind), and `stop_reason` (Stop).
4. **Empty states are honest.** No samples → "No samples yet."; no provider
   calls → "No provider calls recorded." Seeding is never required for the
   page to be correct.
5. **Runtime card is live.** mode · state backend · `tool_count` tools ·
   server version, plus the UI build pin constant (`unigrok-ui-*`) so a
   screenshot always identifies the page revision.

## Styling

Tokens are the shipped 4765 look and stay pixel-faithful (the file wins over
any doc): page `#080b14` + nebula radials, card gradient `145deg #141c33e8 →
#0d1222e8`, hairline `#263252`, cyan `#58e6d9`, blue `#58a6ff`, purple
`#bc8cff`, status green/amber/red `#3fb950 / #f0b95b / #ff6b7a`, bars on
`#202b47` tracks with blue→purple fills, 18 px card radius, 99 px pills,
Inter everywhere. New panes reuse the existing `card` / `bars` / `pill`
primitives — no new visual language.

## Phases

- **P0 — Truth.** Remove the hard-coded `Service ready` claim; render
  `readyz.status`; add a CI regression test so the lie cannot return.
- **P1 — Data.** Add the `kinds` aggregate to `telemetry_summary` (and the
  `/runtimez` benchmark echo) — the last aggregate the schema supports but the
  summary did not expose.
- **P2 — Panes.** Plane mix, Request kinds, Runtime card; Time/Kind/Stop
  receipt columns; verified empty states.
- **P3 — Dev loop.** `seed_dev_telemetry.py` (48 labeled receipts into a
  throwaway local volume), Docker gate (build → compose up → probe →
  screenshot), iterate with the sponsor per round.
- **P4 — Handoff (gated).** After sponsor approval: Codex package for public
  release. Not before.

## Verification gate

Every round before showing work: `uv run ruff check .`, `uv run pytest -q`,
`bash scripts/ci-insider-denylist.sh`,
`uv run python scripts/check_release_contract.py`,
`uv run python scripts/check_docs.py`, `docker compose config --quiet` — then
the Docker rebuild and a live probe of `/ui/`, `/readyz`, `/benchmarkz`.
