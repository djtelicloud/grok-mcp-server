# UniGrok Sleep / Consolidation Plane (hive-merged)

**Miss acknowledged:** the awake-only plan (A0′–A4) described cognition that
starts when MCP arrives. Humans also consolidate while idle. Without a sleep
plane, Docker is half-dead between chats and every live hit pays cold-start tax.

**Hive verdict (telemetry 397):** Sleep is a **real miss as a hemisphere**, but
**overreach as “fill Docker with a second brain.”** Ship it as a **preemptible,
receipted, non-committing maintenance generator** that only decreases a measured
`V_sleep`. Live path owns CommitDone forever. Default sleep **off** until awake
gates are honest.

Companions: `docs/AUTONOMY_INTEL.md`, `docs/DEOVERFIT.md`.  
Needle stays **inactive**; Sleep ≠ Needle (path toward shadow/reflex later).

---

## Dual hemisphere (authority)

```
AWAKE (priority=1)     → assign → G_c → CommitDone to client
SLEEP  (priority=0)    → inventory → rank → act → assess → soft publish → receipt
                         NEVER CommitDone / Needle / host IDE / CLI child
```

| Rule | Detail |
|---|---|
| Preemption | Live queue > 0 → abort sleep; dirty artifacts discarded or marked dirty |
| Live never waits | Missing pack ⇒ cold path; sleep is option value only |
| Priors are soft | Sleep may advise A0′ / A5′ features; **never** proof of A1 or CommitDone |
| Packs | **Skeletons** (schemas, route graphs, prompt templates) — **not answers** |
| Type split | `SleepArtifact` ≠ `LiveResult`; claim-surface detector quarantines terminals |

**Bug direction (do not invert):** live false-reject was *substantial gates on
literal* `MCP_LIVE_OK`. Sleep must **not** “fix” that by also gating dream
success as substantial — and must **not** short-circuit live A1 with ready answers.
Awake A0′/A0 remains: literal class → exact match CommitDone; substantial keeps
structure gates.

---

## What sleep MAY / MUST NOT

### MAY

- Artifact hygiene: rank/prune caches, benches, packs by age/hit-rate/size
- Recompute **public** class/tool priors from *past live* histograms (shadow)
- Pack **skeletons** for top-K predicted classes (empty of private payload)
- Internal benches against **owned fixtures** only (latency/accuracy/cost)
- Rewind/retry failed benches under max-k + ΔV
- Index/compress in-scope traces; warm pools; refresh model catalogs
- Explicit idle receipts (`plane=sleep`, tokens, reason)
- Soft P(next class), P(tools), TTL — advise only

### MUST NOT

- Host project files, IDE buffers, LSP, open tabs
- CLI child attach/inject; Needle activate/promote
- Any client `CommitDone` / success terminal / answer hit-cache that skips A1
- A3/A4 with real external side effects while idle
- Raise/lower live gates from dream success alone (no self-license)
- Cross-session private intelligence merge
- Continuous “use the whole machine” as the objective (cap becomes the target)

---

## Operator loop (collapsed generators)

Human verbs (sort, rank, assess, prune, add, modify, improve, speed-up, bench,
rewind, retry, predict) collapse to one tick:

```
Inventory → Rank → Act → Assess → Publish soft → Receipt
```

`Act ∈ {prune, bench, pack-skeleton, reindex, predict-priors, speed-warm}`.

```
while runtime_alive:
  if live_inflight: preempt(); continue
  if not debt_above_threshold and not event_trigger: nap(); continue
  acquire idle_lease (tokens, wall, $, tool-calls)
  for g in rank(generators, key=ΔV_sleep/cost):
    if live_arrived: break
    checkpoint = snapshot()
    candidate = g.propose()
    if assess(candidate) and E[ΔV_sleep] < -ε:
      publish_soft(versioned)
    else:
      rewind(checkpoint); maybe demote(g)
  emit_receipts(); release_lease()
```

**Prefer event-triggered sleep** (after N live requests, or when C/E/L exceed
thresholds) over always-on fill-the-cap cosplay. Spare CPU is free; tokens are not.

### `V_sleep` (measurable)

| Symbol | Meaning |
|---|---|
| C | Cache/pack bytes × age × (1/hit-rate) |
| E | Predictor calibration error on **past live** outcomes |
| L | p95 cold-start latency for top-K classes (fixture benches) |
| B | Bench debt (failed/stale) |
| R | Receipt/audit backlog |

```
V_sleep = w_C·C + w_E·E + w_L·L + w_B·B + w_R·R
```

Accept operator only if `ΔV_sleep < -ε` and cost ≤ lease and best `ΔV/c`.
If rolling `V_sleep` does not decrease → hygiene-only (prune/receipt).

### Map of human verbs → generators

| Verb | Generator |
|---|---|
| sort / rank | G_sort, G_rank |
| assess | G_assess |
| prune | G_prune |
| add / modify / improve | versioned pack/prior replace after assess + ΔV |
| speed-up | G_pack skeleton + G_bench latency + pool warm |
| bench / rewind / retry | G_bench, G_rewind (max k, exponential cost) |
| predict | G_predict → soft priors + TTL (not answers) |

---

## Predictive ready packs

```
ReadyPack {
  hypothesis_id, predicted_class, shape_hint,
  skeleton: {schemas, route_graph, prompt_templates},  // no filled answers
  confidence, expires_at, version,
  invalidate_on: [live_miss, class_mismatch, A1_fail_spike]
}
```

On live hit: optional warm start / soft A0′ features → **still** run awake
`assign` + `G_c`. Miss → invalidate + `prediction_poison` metric.

“Have its shit together” = lower cold-start + better first quantum, **not**
skipping truth.

---

## Interleaved build order

| # | ID | Plane | Ship |
|---|---|---|---|
| 1 | **A0′ / A0** | Awake | Class assign + literal CommitDone (**first** — live quirk) |
| 2 | **A1 / A1.5 / A5′** | Awake | G_c + unrepairable terminal + class floor |
| 3 | **S0** | Sleep | Scheduler + preempt + energy lease + CPU hygiene (sort/rank/prune/speed) |
| 4 | **S1** | Sleep | Fixture benches + rewind under idle budget (CLI-first) |
| 5 | **A2 / A2.5** | Awake | Lyapunov + recursion |
| 6 | **S2** | Sleep | Predict priors + pack skeletons; hit/miss + K5 rollback |
| 7 | **A3 ↔ S3** | Both | Specialists awake; sleep only rehearses briefs/fixtures |
| 8 | **A4 / S4** | Both | Dynamic tools shadow; Needle still off |
| 9 | **Promote** | Both | Enforce posteriors only with awake⊕sleep dual evidence |

Building dream packs before honest awake gates = autonomy theater.

---

## Kill switches

| ID | Trigger | Effect |
|---|---|---|
| K0 | Live queue > 0 | Abort sleep; no dirty publish |
| K1 | Idle lease exhausted | Park generative ops |
| K2 | `V_sleep` not decreasing | Hygiene-only |
| K3 | Claim surface (CommitDone / LIVE_OK / Needle) in sleep output | Quarantine; trip plane |
| K4 | Host/IDE/CLI/Needle touch attempt | Crash sleep path; incident metric |
| K5 | Live A1 fail ↑ after prior version | Auto-revert prior; blacklist pack gen |
| K6 | `idle_spend / total_spend` > policy | Freeze generative ops |
| K7 | Same generator thrash without accept | Ban for epoch |
| K8 | Proposed `UNIGROK_SLEEP=off` | Plane dark by design until S0 exists |

Flags: `off | cpu | bench | predict` ladder; `UNIGROK_IDLE_BUDGET_*=0` → hard nap.

---

## Metrics

| Metric | Target |
|---|---|
| `dream_client_seal_attempts` / `commit_from_sleep_count` | **= 0** |
| `needle_sleep_touch_count` | **= 0** |
| `sleep_spend_ratio` | Bounded (policy); freeze if heater |
| `ΔV_sleep` / epoch | Decreasing when generative |
| `pack_hit_rate` + `pack_hit_latency_delta` | ↑ only if live outcomes improve |
| `prior_brier` on next live class | ↓ |
| `prediction_poison_rate` / `prior_rollback_count` | Watched |
| `preempt_latency_ms`, `dirty_discard_count` | Healthy preemption |
| Awake `false_continue` / `wrong_class_gate` | Still primary |

If pack_hit_rate low and spend_ratio high → plane is a **heater**; kill generative path.

---

## Failure modes (compressed)

1. Spend runaway (cap becomes target) → event-trigger + ΔV/$ + K2/K6  
2. Stale prediction poisoning → TTL, skeletons-not-answers, K5  
3. Dream → false CommitDone → type split + K3  
4. Needle false claim → no Needle link in sleep; invariant 0  
5. Wrong potential (prune hurts live) → couple to live outcomes  
6. Dual-loop race → COW versions; live pins pack id  
7. Narrative covering broken awake path → **ordering**: awake truth first  
8. Unbounded thought rewind → max k + strict ΔV + K7  

---

## Status

| Item | State |
|---|---|
| Miss named | Done |
| Hive merge | Done (narrow sleep; awake first) |
| Code | Sleep idle loop not started |
| Awake context pack (related) | **Shipped:** `UNIGROK_CONTEXT_PACK=cpu` — inventory → votes → lead merge → ≤2 PFC loops → sealed prefrontal + untrusted `pfc_absent` foresight sibling |
| WASM × dogfood (related) | **Design only:** [WASM_DOGFOOD.md](WASM_DOGFOOD.md) — NOGO gateway runtime; host dogfood `exec` is today’s surface; guest ABI when local sandboxed eval exists |
| Default sleep | Proposed `UNIGROK_SLEEP=off`; no shipping runtime flag yet |
