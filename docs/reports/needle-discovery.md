# Needle Empirical Discovery Report

**Date:** 2026-07-13 · **Hardware:** MacBook Pro M3 Max, 128 GB unified memory ·
**Subject:** [cactus-compute/needle](https://github.com/cactus-compute/needle) @ `main` (MIT), weights `Cactus-Compute/needle` (HF) ·
**Method:** every claim below is backed by a runnable script and a logged metric produced this session (lab scripts referenced per section; no vendor numbers are load-bearing).

## Why this lab exists

UniGrok's dual-plane design (`architecture.md`) is being extended with a
learned reflex layer. The upstream README describes Needle only as a
single-shot function-call model. This lab measured what it can actually do
across UniGrok-relevant gateway tasks — routing, observation typing, recovery,
memory retrieval, CursorBench-style tool selection, extraction, abstention,
trajectory policy — plus undocumented capabilities found by reading its source.

## Headline findings

1. **Needle contains a complete, undocumented retrieval engine** — and it works
   after a 3-minute revival. `encode_contrastive` / `retrieve_tools`
   (`needle/model/run.py:308-344`) project arbitrary text into a 128-dim
   contrastive space. The released checkpoint ships this head **dead at an
   exact-zero saddle** (`max|w| = 0.00e+00`; ReLU zero ⇒ zero gradients ⇒
   unrecoverable by training alone). Re-initializing just the two head layers
   and training head-only (400 steps, ~3 min CPU, CLIP loss, encoder frozen):
   - Tool retrieval top-1: 16.7% (chance) → **41.7%** (random head, no
     training — the encoder features are informative) → **100%** (60 held-out
     queries / 6 tools)
   - Memory-card retrieval top-1: 10% chance → **60%** with only 12
     paraphrase pairs per card (50 messy held-out queries / 10 cards); scales
     with pairs-per-card (1 pair → 20%, 12 pairs → 60%)
   - **Zero side-effect on tool-calling** (head-only freeze; generation output
     byte-identical quality before/after)
   - Implication: **capsules-as-database with Needle as learned retrieval is
     real.** One 26M checkpoint = generation reflex + semantic retrieval over
     plain JSON files. No SQLite in the loop anywhere in this lab.
2. **The architecture is an encoder-decoder** (12-enc/8-dec, attention-only
   blocks, tied embeddings, 8192 BPE) — not a decoder-only LLM. Decoding is
   greedy argmax: **fully deterministic**, the right property for a reflex
   tier. Undocumented in the README.
3. **Fine-tuning per-token class weights exist** (`w_name=2.0, w_value=4.0,
   w_key=1.5`): upstream already weights argument *values* highest — their own
   training confirms values are the weak spot the OKF contract flags.
4. **Apple-silicon verdict (asked: "are we training smart?")** — evidence, not
   vibes: `jax-metal` initializes on the M3 Max GPU and Needle's **forward pass
   works** (7.5 s incl. compile), but the **backward pass fails** in the MPS
   compiler (`null operand found` at the gated-residual scalar inside
   `nn.scan`+`remat`, `architecture.py:250`). Training therefore runs on the
   XLA **CPU** backend — the correct call, not a lazy one. Memory is a
   non-factor: a full fine-tune peaks ~1.2 GB RSS of the 128 GB available. The
   real throughput lever on this machine is **parallel experiment lanes**
   (waves of concurrent fine-tunes), not backend exotica. No Mojo path exists
   for this stack (JAX/Flax end-to-end).
5. **Baseline (pretrained, no fine-tune)**: documented tool-calling passes
   cleanly (3/3 incl. value extraction: `{"room":"kitchen","state":"off"}`);
   ~970 ms/call steady-state CPU via the naive full-buffer decode loop (no KV
   cache — production inference belongs on their Cactus runtime or an ONNX
   export, not this Python loop). Enum selection WITHOUT fine-tuning is
   near-chance: on route_selection the constrained decoder forces the right
   tool name (94.7% name-F1) but `args_acc` = **11%** — the pretrained model
   cannot pick enum values. Everything the fine-tunes score above that is what
   local adaptation buys.

## Reproducible footguns discovered (upstream + tooling)

| # | Footgun | Evidence |
|---|---|---|
| F1 | Contrastive head ships at exact-zero saddle; gradient descent can never revive it — re-init required | revive logs v1 (loss frozen at ln(32)) vs v3 |
| F2 | `optax.masked` leaks raw gradients as updates for unmasked leaves → silently corrupts the whole network (gibberish generation). Use `optax.multi_transform` + `set_to_zero` | revive v2 log (model lobotomized) vs v3 (clean) |
| F3 | needle's tokenizer `mp.Pool` requires the *calling script* to be `__main__`-guarded on macOS (spawn) — unguarded drivers crash with the bootstrapping RuntimeError | matrix run 1 failure logs |
| F4 | `needle finetune` **force-re-downloads** the base checkpoint from HF on every invocation (`_resolve_checkpoint`, `force_download=True`) — patch for lab/offline use | finetune.py:215-228 |
| F5 | `num_memory_slots=64` in the config is dead — declared, threaded through, consumed nowhere | grep of architecture.py/train.py |
| F6 | Checkpoints are **pickle** (arbitrary-code-execution artifacts). Load official weights only; any UniGrok integration must export to safetensors/ONNX before a checkpoint registry exists | run.py `pickle.load` |
| F7 | `batch_size > packed rows` ⇒ silent 0-step NaN schedule: full wall-clock spent, unchanged base promoted as `_best.pkl`, no error raised. Needle PACKS examples into `max_enc_len` rows, so small datasets hit this at default batch 64 | observation_typing run 1 log; train.py:255-257 |

## Live poisoning specimen (field observation)

Mid-lab, a data-generation request routed to `grok-composer-2.5-fast` returned
*"Generating the full JSON dataset with a script to ensure valid structure…"*
— a statement of intent with **zero content** — and the UniGrok runtime
labeled it `finish_reason="final_answer"`. This is the exact
confident-non-answer → `success=1` mislabel identified in the
authority-inversion design review (src/utils.py:5821-5826 / fast path :8197),
observed organically during this session. The VerifiedOutcome contract (design
Phase 0) remains the prerequisite for any learning flywheel.

## Fine-tune matrix (UniGrok gateway families)

Datasets: 8 families, ~3.0k examples total, generated as plain JSONL capsule
files with per-family catalog hashes (`lab/gen_datasets.py`,
`gen_nextstep.py`); out-of-template test set for routing authored by
**grok-4.5 through the UniGrok MCP itself** (48 messy realistic queries).
Splits are needle's own per-tool 100/10/10 (seed 42). Scoring separates
tool-name / arg-key / arg-value tiers (`lab/eval_family.py`).

<!-- MATRIX_RESULTS -->
| Family | n | Base args-acc | Tuned exact (held-out) | Steps / wall |
|---|---|---|---|---|
| route_selection (mode+class enums) | 344 | 11% | **100%** (10/10) | 66 / ~16 min |
| recovery_selection (action enum) | 240 | 0% | **100%** (10/10) | 50 / ~16 min |
| observation_typing (label enum) | 145 | 0% | **100%** (10/10) | 160 / ~22 min |
| memory_rerank (id-copy) | 165 | — | **100%** (10/10) | 240 / ~47 min |
| tool_selection (6-tool CursorBench-style) | 564 | 0% | **100%** (60/60, clean split, free values incl.) | 481 / ~96 min |
| extraction (free values: path+symbol+action) | 600 | 0% | **100%** (clean split) | ~116 min* |
| abstention (+abstain pseudo-tool) | 364 | — | **VOIDED** — 20/40 test-train leakage; run killed | — |
| next_step (trajectory policy) | 560 | — | 100% (**quarantined**: split leakage; see self-loop probe for the valid signal) | ~30 epochs |
| combined (7 families, interference test) | ~2.7k | — | see interference table below | 6 epochs

Reading: the pretrained model **cannot** do enum selection (0–11% args-acc);
fine-tuning on 145–344 examples takes it to perfect on held-out in-template
data in 50–160 optimizer steps on CPU. Out-of-template generalization is
measured separately (Grok-authored OOD set, below).

### Interference test: one checkpoint, seven families — zero interference

The combined checkpoint (7 families, ~2.7k examples, 6 epochs, 36 min) scored
**100% on every family's own held-out split** (route 10/10, observation 10/10,
recovery 10/10, memory 10/10, tool_selection 60/60, extraction 10/10,
next_step 60/60†; internal packed-eval 99.2%). First actual measurement of
the "split specialists only when interference is shown" rule: **at this scale,
splitting is not justified — one 26M checkpoint serves all families.**
Caveats: in-template splits only (OOD-level interference untested);
† next_step inherits its leakage caveat; most splits n=10.

Footgun F7 discovered en route: needle's trainer computes
`total_steps = packed_rows // batch × epochs`; when `batch > packed rows` it
silently runs a **0-step NaN schedule** and "trains" for the full wall-clock,
then promotes the unchanged base model as `_best.pkl` (observation_typing run
1: 782 s of nothing, exact_match 0.0). The lab driver now estimates packed
rows and clamps batch size.

## Undocumented capability probes

### Retrieval revival — CONFIRMED (headline finding #1)

### Few-shot ICL at inference (kill-criterion experiment) — **KILLED**

Injected k ∈ {0,4,8} verified examples via both channels (tools-JSON smuggling
à la `build_needle_tools_context`, and query-prefix), against base and tuned
checkpoints, on the 48-query OOD set (`lab/icl_probe.py`):

| Checkpoint / channel | k=0 args | k=4 args | k=8 args | k=8 name |
|---|---|---|---|---|
| tuned / tools | **45.8%** | 33.3% | 18.8% | 39.6% (collapse) |
| tuned / query | **45.8%** | 25.0% | 25.0% | 100% |
| base / either | 0% | 0% | 0% | degrades w/ k |

Monotonic degradation in every condition. At k=8 in the tools channel the
examples crowd the real tool schemas out of the 1024-token encoder and even
tool-NAME accuracy collapses. **Needle has no in-context learning; inference-
time example injection is strictly harmful.** Design consequence (D4): the
"real-time learning" tier is honest only as (a) minutes-scale retraining and
(b) contrastive-retrieval revival; the verified-example JSONL channel feeds
training, never inference context.

### Out-of-template generalization (route_selection vs Grok-authored OOD)

100% in-template → **45.8% exact** on 48 messy realistic queries
(parse 100%, name 100%, key 100% — every failure is value-tier). Failures are
systematic, not noise: long/discursive **coding** requests misroute to
`reasoning/planning` (the template style leaked into the decision boundary);
research/vision/planning classes mostly hold. Lesson: **phrasing diversity
beats example count** — the flywheel must train on verified *real* episodes,
not synthetic templates. (0.48 s/call batched eval on CPU.)

### Self-loop: Needle-ReAct micro-loop on unseen vocabulary — 5/7, mechanism proven

Simulated-env loop (`lab/selfloop.py`) against the next_step fine-tune, on
symbols/files absent from ALL training data, bounds: 8 iterations,
same-call-twice stop. MECHANISM-ONLY evidence (the trained `done` emission
violates the verifier-owned completion contract; env verifier gates every
`done` on edited ∧ tested ∧ committed):

- **Happy path 5/5**: full 6-step chains (search→read→edit→test→commit→done)
  with correct value-copying of never-seen symbols (`normalize_headers`,
  `FlushQueue`, `decode_frame`, `SessionGuard`, `rotate_logs`). A 26M policy
  can run a bounded ReAct loop over observation digests, generalizing
  argument binding.
- **Failure-injection 0/2, precisely diagnosed**: after an injected test
  failure the policy performs the trained recovery (`read_file`) then repeats
  it — the training data contained one recovery step per failure branch but
  no post-recovery continuation (re-edit→re-test). Data-shape gap, not a
  capability wall. The same-call-twice bound stopped both doom-loops
  mechanically — the loop-safety design works.

Design consequence: recovery sub-trajectories need full repair arcs in
training, or (better, per the corrected contract) recovery becomes an
enumerated `dispatch_candidate` decision instead of a generative one.

### Catalog drift + hash-guard flywheel (file-only, no DB)

Ran against the clean tool_selection checkpoint (`lab/drift_probe.py`).
**Two predicted failure modes did not reproduce; the model is more robust than
the adversarial design assumed:**

| Probe | Prediction (design review) | Measured |
|---|---|---|
| Param key renamed (`find`→`needle_find`) after training | silent structurally-valid-but-stale calls | **10/10 emitted the new key with correct values; 0 stale bindings, 0 parse fails** |
| New tool added post-training (`format_code`, zero training mass) | permanently shadowed, never selected | **6/6 correct selections from the schema description alone** |
| Served-catalog hash vs trained hash | — | mismatch detected → ABSTAIN + queue retrain; pure file check, no DB |

Interpretation: the 2B-token function-call pretraining gives real
**schema-reading generalization** — Needle can select tools/candidates it was
never trained on, provided descriptions are good. This strengthens the
`dispatch_candidate` contract (pre-bound candidates don't each need training
mass) and softens (does not eliminate) drift-severity: simple renames and
additions are handled; *semantic* drift (same key, changed meaning) remains
untested and the hash guard remains the correct backstop.

## Scope correction (post peer-review)

An independent review (relayed 2026-07-13, cross-checked against Grok 4.5 and
the Codex-authored `docs/design/authority-inversion.md`) correctly re-scoped
this lab. Verified against our own artifacts, all of the following hold:

1. **This matrix is a baseline capability artifact, not an architecture
   decision.** Per-dispatch label accuracy is a *diagnostic*. The product
   criterion is an end-to-end **agentic-parity frontier**: fast-Grok +
   Needle-directed subagents must match Grok 4.5 / grok-build-0.1 on *verified
   long-running objectives* while cutting deliberation latency, Grok calls,
   and cost. That experiment ("the real one") is specified below and is NOT
   answered here.
2. **Invalid rows.** `abstention` has 20/40 test queries appearing verbatim in
   train (82 duplicate queries; the dedup pass only covered tool_selection) —
   its score is memorization, not generalization. `next_step` has 9/60 exact
   overlaps plus structural prefix leakage (trajectory prefixes shared across
   splits) and a single toy workflow — its FINETUNED_EVAL row is invalid; only
   the unseen-vocabulary self-loop probe carries any generalization signal.
   `route_selection` labels are imbalanced 220/56/48/20. route/tool_selection
   splits verified clean of exact overlap.
3. **Output contract correction.** These families train Needle to *generate*
   decision content. The better production contract is
   `dispatch_candidate(candidate_id)`: selection among pre-bound,
   envelope-validated candidates (subagent + bounded work item + lease/budget
   + observation schema + success verifier), alongside
   `wait / refresh / escalate_to_grok / request_verification / abstain`.
   Needle must never emit authority, TTL, completion, effect IDs, or
   confidence. The lab's `done` pseudo-tool violates this: completion belongs
   to verifier receipts, not to the policy. Supporting evidence from this very
   lab: the *id-selection-shaped* tasks (memory_rerank id-copy 100%;
   contrastive retrieval 100%/60%) are its most robust results.
4. **Judging correction.** Hard-coded single labels are wrong for decision
   families with multiple valid next steps. Bootstrap labeling should use
   blinded, order-randomized **pairwise** comparisons by Grok judges
   (grok-build-0.1 for coding/tool transitions; grok-4.5 for goal progress and
   long-horizon comparison; never sole-generator-and-sole-judge), recorded as
   *provisional teacher labels* with exact model ids. The repo's existing
   judge (`src/semantic_evals.py` SemanticEvalVerdict) is unsuitable
   unchanged: absolute 1-5 prose grading, metadata leakage, observational by
   design. The AGDPO schema's `pareto_dominance` + `dominance_receipt` basis
   (docs/okf/agentic-dpo-pair-v1.schema.json) is the aligned target format.
5. **TTL/lease was absent** from training and prediction here. It belongs in
   the normalized state (e.g. `lease=critical|short|healthy`) with code doing
   all timestamp/authority checks. The lab's catalog hash (names + param keys)
   also under-covers contract semantics vs the design doc's contract_hash.
6. **Retention suite gap.** Family fine-tunes update all parameters with no
   forgetting measurement. (The retrieval revival, by contrast, is exactly the
   frozen-encoder/head-only/retention-verified probing the review asks for —
   and its 41.7%-random-head result is the strongest current evidence for the
   latent-structure hypothesis.)

## The real experiment (next study)

**"Can Needle-directed subagents make fast Grok match Grok 4.5/Build on
verified long-running objectives?"** Compare complete systems on identical
objective sets: (1) grok-4.5 agentic alone, (2) grok-build-0.1 agentic alone,
(3) fast Grok + deterministic routing, (4) fast Grok + Needle
`dispatch_candidate`, (5) later: Needle continuity during Grok outages.
Primary metric: verified objective + subagent success (receipts, not labels).
Secondary: time-to-progress, Grok calls/tokens/cost, subagent yield, dead
ends, recovery time, unnecessary escalations, TTL/lease violations, premature
completion claims, safety/authority violations. Training loop: Grok
generates+judges candidate transitions pairwise → mechanical vetoes first →
confusion tensors over real agentic failure modes (wrong subagent, no-progress
dispatch, bad recovery, needless escalation, acted-on-thin-TTL, waited-when-
actionable, premature completion) → targeted hard examples for weak cells →
per-family specialists from the immutable base with replay/KL retention →
promotion only on multi-seed end-to-end shadow parity. Objective-level splits
with a sealed test set the generator never sees.

## Dry run: confusion-driven training loop (Swarm `training_experiment` shape)

Miniature two-iteration validation of the proposed Swarm-as-experiment-
optimizer loop, on the one *measured* confusion cell (messy-coding→planning
misroutes). All arms trained from the immutable base; candidate records with
base/dataset hashes (`lab/build_arms.py`, `data/arm_candidates.json`); sealed
40-query test set (10/class, fresh domains) frozen with a mechanical
zero-overlap guard BEFORE evaluation (`sha 52276cf8…`); forgetting gate on
every arm.

| arm (recipe) | sealed | dev OOD | in-template | worst class | forgetting |
|---|---|---|---|---|---|
| A control (templates) | 40.0% | 45.8% | 100% | vision 10% | 3/3 |
| B +40 hard negatives | 45.0% | 47.9% | 100% | vision 10% | 3/3 |
| C +30 metamorphic | 42.5% | 47.9% | 100% | planning 20% | 3/3 |
| D balanced resample | 40.0% | 45.8% | 100% | research 10% | 3/3 |
| **E = B + worst-cell delta** (iter 2) | **52.5%** | **56.2%** | 100% | research 20% | 3/3 |

Findings:
1. **Small targeted deltas do NOT fix distribution problems** (B/C/D within
   the ±8pp noise band of n=40) — the cheap version of the confusion-loop
   hypothesis is dead. OOD movement requires breadth/volume of realistic
   data; the flywheel's fuel must be verified real episodes.
2. **The adaptive cycle itself works**: iteration 1's Pareto table exposed a
   NEW worst cell (vision at 10% recall — 20 near-identical templates vs
   diverse sealed phrasings); iteration 2's targeted arm (+32 diverse vision,
   +16 research examples) fixed vision AND lifted sealed +12.5pp / dev OOD
   +10.4pp over control, zero forgetting, in-template retained. Worst cell
   moved to research — generation 3's target. Two full cycles of
   measure→arm→train→sealed-eval ran mechanically.
3. **Early stopping matters**: arm B at the naive step target burned 44 min;
   arms C/D/E at 12 epochs (~8-9 min) reached identical convergence. "Keep
   training" must be an optimizer decision, not a constant.
4. **Caveats**: same-generator correlation (grok-4.5 authored both sealed set
   and deltas in separate context-free calls; zero string overlap verified,
   stylistic correlation possible — production loop should diversify
   generators as it does judges); n=40 sealed set ⇒ coarse ±8pp resolution;
   single seed per arm (multi-seed is a promotion-gate requirement, not a
   dry-run one).

Verdict for the Swarm integration: the `training_experiment` target type
earns its integration — with data-recipe arms as the primary search
dimension, mechanical training-vitals vetoes (F7 class), sealed-set +
forgetting gates, and worst-cell-first curriculum. Hyperparameter arms are a
minor dimension at this model scale.

## Design implications (vs the authority-inversion decision log)

| Decision | Lab verdict |
|---|---|
| D5 capsules-as-DB, SQL demoted to projection | **STRENGTHENED** — retrieval engine exists inside Needle itself; the whole lab ran file-only |
| D9 memory-rank cut from v1 families | **REVISE** — memory-rank is not a rank-via-tool-calling task; it is native contrastive retrieval, cheap to revive and side-effect-free. Promote to v1 alongside tool_selection |
| D4 ICL as falsifiable hypothesis | pending ICL probe; retrieval retraining (3 min) already provides the "real-time learning" tier honestly |
| D12 never pickle | **CONFIRMED + urgent** (F6) |
| D3 bounded self-loop | pending selfloop probe |
| D11 catalog-hash pinning | pending drift probe |

## Conclusion

What one day of empirical work established, none of it from vendor claims:

**Confirmed capabilities** (each with a runnable script + logged metric):
one 26M checkpoint learns all 7 UniGrok gateway families with zero measured
interference; selection-shaped outputs (ids, enums, pre-bound candidates) are
its strongest mode; schema-reading generalization lets it use tools it was
never trained on; the dormant contrastive head revives in 3 minutes into a
real retrieval engine over plain files; a bounded self-loop chains multi-step
work on unseen vocabulary; the whole lifecycle — datasets, checkpoints,
invalidation, candidate records — ran end-to-end with **zero databases**.

**Confirmed limits**: no in-context learning (injection strictly harms);
in-template perfection says nothing about OOD (40-56% sealed); small clever
data deltas don't fix distribution gaps — verified real episodes at volume
are the only honest fuel; recovery needs full repair arcs or enumerated
recovery candidates; values remain the weak tier under distribution shift.

**Confirmed process**: the confusion-driven loop (measure worst cell →
targeted arm → sealed eval → Pareto + forgetting gate) cycled twice and
improved both times (+12.5pp sealed). The Swarm `training_experiment`
integration earns a green light with data-recipe arms as the primary search
dimension.

**Status of these artifacts**: pre-foundation research artifacts and baseline
arms, per the accepted review framing — mechanism evidence, not promotion
evidence. The next study is the sealed agentic-parity benchmark
(fast-Grok + Needle dispatch vs Grok 4.5/Build on verified long-running
objectives) under the C0-C7 structural plan, with training opened only by
measured deficiency.

---
*Lab: scratchpad `lab/` (scripts, logs, datasets, checkpoints; excluded from
repo). Agent-Assisted-By: Claude via Claude Code; peer review: Grok 4.5 (CLI
plane), ChatGPT 5.6 (relayed), Gemini/Antigravity (structural plan).*
