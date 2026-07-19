# UniGrok de-overfitting plan

Hive-merged (ultra, mission-committed, ~$0.034). Doctrine:

> Freeze only near-physics envelopes. Everything else is a posterior:
> shadow-measured, budget-capped, promotion-receipted, kill-switchable.

Needle stays **visibly inactive** until a real shadow/reflex runtime, tests,
promotion boundary, and provenance receipts exist.

## Verdict

**OVERFITTED.** Ship a freeze-envelope / unfreeze-posterior split.

| Layer | Current sin | Correct status |
|---|---|---|
| Governor weights / regex → risk | Frozen posteriors as law | Shadow → demote → delete as authority |
| Sync windows, pool sizes, fixed timeouts | Tuned constants as structure | Quantile/load posteriors under caps |
| `inspect.getsource`, `_JOB_TASKS` shape, semaphore identity tests | Mechanism lock-in | Contract tests only |
| Closed tool surface | Over-conservatism as product | Open under sandbox + envelope + promotion |
| Fencing/CAS, spend/time/bytes, no-exfil, public boundary | Real physics | Never dynamic without dual fence |

Ruthless rule: if a change requires tests to know *how* something works rather
than *what* is guaranteed, the test (and often the design) is overfit.

## Phases (kill switches)

### Phase 0 — Envelope freeze + inventory
- Named physics module: CAS/fencing, spend/time/bytes ceilings, secret non-exfil,
  no local shell in CLI child, independent evidence for promote.
- Inventory magic numbers; tag `PHYSICS | POSTERIOR | BRITTLE_TEST | CLOSED_SURFACE`.
- Feature flags: `shadow | enforce | off` per subsystem.

**Kill:** non-physics paths restore today’s behavior. Physics never rides the flag.

### Phase 1 — Demote governor (shadow scores, legacy still enforces cognition display)
- Split: signal extractors (features) → versioned weight bundle (posterior) →
  envelope gate (physics hard-deny only).
- Dual records: `legacy_decision` vs `shadow_decision` + redacted features +
  weight version. Dual-log **fail-open** and **idempotent**.
- No enforcement change from new scores yet.

**Kill:** `UNIGROK_GOVERNOR_SHADOW=0` stops shadow logs.

### Phase 2 — Delete mechanism tests (NOW)
| Pattern | Action |
|---|---|
| `inspect.getsource` | DELETE → behavioral detach contracts |
| `_JOB_TASKS` membership/shape | DELETE → job queryable / complete / cancel contracts |
| `semaphore.locked` / object identity | DELETE → concurrency property under load |
| Private `_` imports as merge gate | DEMOTE to non-gating debug |

**Exit:** default CI has zero `getsource`, zero `_JOB_TASKS` structural asserts,
zero semaphore identity checks.

### Phase 3 — Unfreeze timeouts & pools
- `timeout = min(hard_cap, max(floor, f(quantile)))`
- Pool sizes clamped by load under physics max.
- Shadow-apply first.

**Kill:** `DYNAMIC_TIMEOUTS=0` / `DYNAMIC_POOLS=0`.

### Phase 4 — Open tool surface under hermetic sandbox + promotion
- Lifecycle: propose → hermetic dry-run → shadow canary → promote with receipt → enforce.
- Hive may *generate*; runtime load requires promotion receipt.
- Needle: real shadow/reflex only; no false “active” claim.

**Kill:** `DYNAMIC_TOOLS=0`; `NEEDLE_RUNTIME=off` (default).

### Phase 5 — Enforce posteriors
- Promote shadow governor inside envelope; keep legacy in shadow for regression.
- Auto-revert on error/spend/divergence spikes.

**Kill:** `POSTERIOR_ENFORCE=0`.

## Never dynamic without sandbox + budget fence

1. Public process boundary (CLI child shell/FS).
2. Secret material (keys, tokens, raw memory payloads in logs).
3. Hard spend / wall-time / bytes ceilings.
4. CAS / lease fencing correctness.
5. Credential plane separation (CLI OAuth vs API key).
6. Promotion without independent evidence.

## Success metrics

- Mechanism tests gone from default CI.
- Magic weights not scattered as control-flow authority (versioned bundle only).
- Dual governor logs on ≥N missions with measurable divergence.
- Timeouts/pools have hard floor/ceiling + versioned defaults.
- Zero false Needle runtime claims.
- Any generated tool has a promotion receipt trail before load.

## Status

| Item | State |
|---|---|
| Phase 2 contract-test rewrite | In progress this change |
| Governor weight bundle (demote literals) | In progress this change |
| Physics envelope module | Stub this change |
| Phases 3–5 | Not started |
