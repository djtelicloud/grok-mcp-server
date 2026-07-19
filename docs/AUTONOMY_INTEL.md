# UniGrok Autonomy + Intelligence Plan (hive-merged)

**Hive verdict: REVISE → ship A0′/A0/A1/A1.5 first; hold A3–A4 until commit truth is green.**  
Critique: high/direct, mission-committed (`telemetry_id` 396). Ultra critique job may still be in deadline quanta — treat this merge as authoritative for sequencing.

Companions: `docs/DEOVERFIT.md`, **`docs/SLEEP_PLANE.md`** (idle consolidation —
the hemisphere this plan originally missed). Goal: max useful autonomy in Docker
*awake and asleep*; return what IDE/human asked; **error physics hard**; quality
gates are class-conditional posteriors.

---

## Proven anomaly (live)

| Fact | Detail |
|---|---|
| Task | Reply with exactly `MCP_LIVE_OK` |
| Model | Correct: `MCP_LIVE_OK` |
| Mission V2 | `continue`, `committed=false` |
| Gaps | `answer_too_short`, `token_echo`, `insufficient_evidence` |
| Why stuck | No legal repair under that acceptance → continue has no progress |

**Root cause (hive):** class-mismatched verify — **substantial** gates fired on a **literal** mission. Not “model too short.”

---

## Doctrine

```
Sₙ₊₁ = T(Sₙ)     # epoch
Commit ⇔ Accept(G_c, candidate)
Continue ⇔ ∃ repair ∧ ΔV ≤ −ε ∧ state_hash novel
else Terminal (Commit | Fail)
```

### Task class (assign at mission create)

```
c ∈ {literal, echo_ok, receipt, substantial, adversarial}
```

- Lattice is a **strength chain**, not a random partition — but runtime needs a
  **deterministic `assign(task, acceptance)`** at start.
- Downcast freely; **upcast only with explicit escalate(reason)**.
- Closed-form live probes (`MCP_LIVE_OK`, `PONG`, status enums) force `literal`
  at create — do not “discover” class mid-loop.
- **`G_c` runs only checks with floor ≤ c.** Wrong-class gates must never fire
  (metric: `wrong_class_gate_rate → 0`).

### Verify generator (co-emit repairability)

```
G_c(ctx) → [(check_id, predicate, repair | ⊥)]
```

| On fail | Action |
|---|---|
| `repair = ⊥` | Terminal (Commit if alternate accept, else Fail) — **never Continue** |
| `repair` set | Continue only if repair is scheduled **and** `V` decreases |

**Literal `G_literal`:** exact / allowlist / regex only.  
**echo_ok:** short OK; **never** anti-echo.  
**receipt:** tool/ledger digest.  
**substantial / adversarial:** today’s structure + evidence (+ strict depth when enforced).

### Potential `V(X)` (A2 — real, not marketing)

State `X = (class, remaining_repairable_checks, evidence_set, tool_budget, specialist_budget, candidate_hash)`.

Example discrete potential:

```
V = α·unsatisfied_repairable + β·unexplored_decomp + γ·unused_evidence_slots + δ·specialist_quota
```

**Forbidden:** same `V`, same failing check set, same candidate class → Continue
(this *is* the `MCP_LIVE_OK` loop). Same-state hash repeat ≥2 → terminal.

### Recursion contract (A2.5)

```
if c ≤ echo_ok ∧ Accept → return          # no decompose
if needs_structure ∧ c ≥ receipt:
  subs = decompose  # each class_i ≤ parent unless escalate
  fold(evidence) under G_parent only
```

No infinite decomp of literals. No class inflation on children. Fold must not
re-string-join into a shape that fails a higher-class parent gate incorrectly —
**parent class dominates**.

### Specialists (A3 — after commit truth)

Typed sub-missions with own `G_{c_sub}`, early stop when parent `V` drops,
negative utility = spawn that does not reduce `V`. **No spawn for literal/echo_ok.**

---

## Phases (ordered kill switches)

| # | Phase | Ship | Kill |
|---|---|---|---|
| **1** | **A0′ Class assign** | Deterministic `assign` + force-literal for closed probes; instrument wrong-class gates | `UNIGROK_TASK_CLASS=off` → treat all as substantial (legacy) |
| **2** | **A0 Literal CommitDone** | If `c∈{literal,echo_ok}` and match → CommitDone + structural literal-match evidence; skip length/echo/essay evidence | `UNIGROK_VERIFY_LITERAL=0` |
| **3** | **A1 Strategy G_c** | Replace flat `_structural_gaps` with class-indexed generator; property tests | `UNIGROK_VERIFY_STRATEGY=legacy` |
| **4** | **A1.5 Unrepairable map** | `repair=⊥` → FailDone\|CommitDone; same-state continue detector | always-on once A1 ships; flag to log-only |
| **5** | **A5′ Class-floor enforce** | Soft scores never veto literal accept; refuse higher-class checks | `POSTERIOR_ENFORCE=0` |
| **6** | **A2 Lyapunov continue** | Continue iff repair + ΔV + novel state; log V | `UNIGROK_CONTINUE_LYAPUNOV=0` |
| **7** | **A2.5 Recursion** | Decomp/map/fold with class inheritance + depth in V | `UNIGROK_RECURSION=0` |
| **8** | **A3 Specialist missions** | Nested missions, not JSON skins; utility = ΔV/cost | `UNIGROK_SPECIALIST_MISSIONS=0` |
| **9** | **A4 Dynamic tools** | Sandbox → promote; tools emit typed receipts | `DYNAMIC_TOOLS=0`; Needle off |

**Ship gate before A2+:** A0′+A0+A1+A1.5 green on literal goldens **and** one substantial task that still rejects empty/nonanswer.

---

## What to delete vs invent

| Delete / demote | Invent |
|---|---|
| Universal ≥8 words as CommitDone law | `assign` + `G_c` with repair\|⊥ |
| Universal `token_echo` ban | Literal-match evidence record |
| Blind continue on unrepairable gaps | Same-state / ΔV kill-switches |
| Aggregate “quality/length” north star | Class-stratified metrics |
| Hive-as-prompt-skins as end state | Specialist sub-missions after A0–A2 |

---

## Success metrics

| Metric | Target |
|---|---|
| `false_continue` by class (esp. literal) | → 0 on goldens (**primary regression**) |
| `wrong_class_gate_rate` | → 0 |
| `unrepairable_continue_rate` | **= 0** (invariant) |
| `commit_rate` by class | Literal → ~1.0 on goldens; substantial calibrated |
| `false_commit` by class | Bound; do not trade for literal ease |
| `repair_success_rate` | Continues that close ≥1 check |
| `ΔV≤0 continue_rate` | → 0 after A2 |
| `ask_alignment` / return_shape_match | Token vs essay as asked |
| Specialist `ΔV / spawn_cost` | > 0 in shadow or don’t spawn |
| Recursion `basecase_hit_rate` | High for literal |

**Drop as north stars:** aggregate commit_rate alone; loop_depth without false_continue/ΔV; vague specialist counts; global length/novelty scores.

---

## Immediate engineering slice (P0) — done

1. `assign_task_class` + framed literal extract + dual-intent / adversarial precedence
2. Early CommitDone in `mission/verify.py` **and** `autonomy.check_propose_done`
3. A1.5 `should_terminal_fail` (same-state / second literal_mismatch → FailDone)
4. Contract tests + Docker live: `MCP_LIVE_OK` commits with `task_class=literal`

---

## North star (compressed)

Safest autonomy ≠ most rules. It means:

1. Physics never soft (lease, hash, spend, planes, no silent lost work).
2. Intelligence = **right verify generator for the ask**, recursive only when the
   class needs structure, specialists only when they reduce `V`.
3. Continue is an **iterator step with proven progress**, not a hope loop.
4. **Target design:** pair awake CommitDone truth with a preemptible sleep
   consolidation plane (sort/rank/bench/rewind/predict) under idle budgets. The
   shipping Docker runtime currently implements the awake plane only; sleep remains
   a design.
5. The IDE/human gets **exactly the shape they asked for**, often with warm packs
   already ranked — without skipping awake `G_c`.

---

## Status

| Item | State |
|---|---|
| Live anomaly | Confirmed |
| Hive critique (awake) | Merged (REVISE sequencing) |
| Sleep / consolidation plane | Specced in `docs/SLEEP_PLANE.md` (miss closed in docs) |
| A0′ / A0 code | Implemented (`mission/task_class.py` + verify/autonomy) |
| A1.5 unrepairable / same-state FailDone | Implemented (`should_terminal_fail` + epoch) |
| S0 idle scheduler | After A0′/A0 ship gate |
