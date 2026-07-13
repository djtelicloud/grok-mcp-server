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
   - **Baseline check (audit-added): trivial lexical retrieval beats the
     revived head on the same memory benchmark** — word TF-IDF 86%, char-3gram
     TF-IDF 92% vs the head's 60% (top-1, identical 50 queries/10 cards;
     deterministic reproduction from committed data:
     `evals/needle_lab/tfidf_baseline.py` prints 86–96% across analyzer/
     idf-fit variants, every variant beating the head — log:
     `logs/tfidf-baseline.log`).
     Corrected implication: the revived head is a **candidate shadow ranker**,
     not a database replacement. The *file-only lifecycle* (datasets,
     manifests, invalidation, candidate records — no SQLite anywhere in this
     lab) is demonstrated; the learned component is not yet justified over
     deterministic lexical retrieval at this scale.
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
   XLA **CPU** backend — the correct call, not a lazy one. (Log caveat: the
   decisive forward-OK/backward-fail run was never redirected to a file; the
   only saved metal log is an earlier probe that failed on a missing
   dependency — `logs/metal-probe-FAILED-moduleerror.log`. These figures are
   session-observed; see `logs/README.md`.) Memory is a
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
| F1 | Contrastive head ships at exact-zero saddle; gradient descent can never revive it — re-init required | `logs/revive-v1-frozen-saddle.log` (loss frozen at ln 32) vs `logs/revive-v3-clean-revival.log` |
| F2 | `optax.masked` leaks raw gradients as updates for unmasked leaves → silently corrupts the whole network (gibberish generation). Use `optax.multi_transform` + `set_to_zero` | `logs/revive-v2-gradient-leak-lobotomy.log` vs `logs/revive-v3-clean-revival.log` |
| F3 | needle's tokenizer `mp.Pool` requires the *calling script* to be `__main__`-guarded on macOS (spawn) — unguarded drivers crash with the bootstrapping RuntimeError | matrix run 1 failure (log not preserved — overwritten by run 2's redirects; see `logs/README.md`) |
| F4 | `needle finetune` **force-re-downloads** the base checkpoint from HF on every invocation (`_resolve_checkpoint`, `force_download=True`) — patch for lab/offline use | finetune.py:215-228 |
| F5 | `num_memory_slots=64` in the config is dead — declared, threaded through, consumed nowhere | grep of architecture.py/train.py |
| F6 | Checkpoints are **pickle** (arbitrary-code-execution artifacts). Load official weights only; any UniGrok integration must export to safetensors/ONNX before a checkpoint registry exists | run.py `pickle.load` |
| F7 | `batch_size > packed rows` ⇒ silent 0-step NaN schedule: full wall-clock spent, unchanged base promoted as `_best.pkl`, no error raised. Needle PACKS examples into `max_enc_len` rows, so small datasets hit this at default batch 64 | observation_typing run 1 (782 s, exact_match 0.0; log overwritten by run 2 — `logs/ft-observation-typing-RUN2-batch8-1338s.log` is the clamped rerun); train.py:255-257 |

## Live poisoning specimen (field observation)

Mid-lab, a data-generation request routed to `grok-composer-2.5-fast` returned
*"Generating the full JSON dataset with a script to ensure valid structure…"*
— a statement of intent with **zero content** — and the UniGrok runtime
labeled it `finish_reason="final_answer"`. This is the exact
confident-non-answer → `success=1` mislabel identified in the
authority-inversion design review (at lab time: `success = 1 if
layer.finish_reason == "final_answer" else 0`), observed organically during
this session. Since the lab ran, main has landed the first VerifiedOutcome
implementation — `_verified_outcome_label` keeps `final_answer` **unverified
(NULL)** and marks only gateway-detectable failures as 0 (src/utils.py:5212
at this head) — plus promise-only completion detection and recovery, so this
exact specimen would no longer be labeled a success. The full VerifiedOutcome
contract (design Phase 0) remains the prerequisite for any learning flywheel;
this landing validates, not closes, that requirement.

## Fine-tune matrix (UniGrok gateway families)

Datasets: 8 families, ~3.0k examples total, generated as plain JSONL capsule
files with per-family catalog hashes (`evals/needle_lab/gen_datasets.py`,
`gen_nextstep.py`); out-of-template test set for routing authored by
**grok-4.5 through the UniGrok MCP itself** (48 messy realistic queries).
Splits are needle's own per-tool 100/10/10 (seed 42). Scoring separates
tool-name / arg-key / arg-value tiers (`evals/needle_lab/eval_family.py`).

<!-- MATRIX_RESULTS -->
| Family | n | Base args-acc | Tuned exact (held-out) | Steps / wall |
|---|---|---|---|---|
| route_selection (mode+class enums) | 344 | 11% | **100%** (10/10) | 66 / ~16 min |
| recovery_selection (action enum) | 240 | 0% | **100%** (10/10) | 50 / ~16 min |
| observation_typing (label enum) | 145 | 0% | **100%** (10/10) | 160 / ~22 min |
| memory_rerank (id-copy) | 165 | — | **100%** (10/10) | 240 / ~47 min |
| tool_selection (6-tool CursorBench-style) | 564 | 0% | **100%** (60/60, clean split, free values incl.) | 481 / ~96 min |
| extraction (free values: path+symbol+action) | 600 | 0% | **100%** (clean split) | 480 / ~116 min* |
| abstention (+abstain pseudo-tool) | 364 | — | **VOIDED** — 20/40 test-train leakage; run killed | — |
| next_step (trajectory policy) | 560 | — | 100% (**quarantined**: split leakage; see self-loop probe for the valid signal) | ~30 epochs |
| combined (7 families, interference test) | 2,618 | — | INVALID — see interference section below | 6 epochs |

\* extraction's wall-clock ran concurrently with the abstention lane (killed
mid-run when the leakage was found); the comparable single-lane run is
tool_selection at 481 steps ≈ 96 min. Log: `logs/ft-extraction-480steps-6983s.log`.

Reading: the pretrained model **cannot** do enum selection (0–11% args-acc);
fine-tuning on 145–344 examples takes it to perfect on held-out in-template
data in 50–160 optimizer steps on CPU. Out-of-template generalization is
measured separately (Grok-authored OOD set, below).

### Interference test — **RESULT INVALID (split contamination; audit-confirmed)**

The combined run concatenated family JSONLs and let the trainer re-split the
combined file (seed 42) — which selected *different* test rows than the
per-family splits. Verified contamination of the per-family test rows inside
the combined TRAINING data: observation 7/10, recovery 10/10, memory 8/10,
tool_selection 51/60, extraction 10/10, next_step 56/60 — **only routing
(0/10) stayed clean**, and routing scored 100% either way. (Deterministic
reproduction from committed data:
`evals/needle_lab/check_combined_contamination.py`; log:
`logs/combined-contamination-check.log`.) The combined
checkpoint's per-family scores therefore measure memorization of trained
rows, not interference. **No conclusion about one-vs-many checkpoints can be
drawn from this run.** Production Needle training remains separated by exact
output-contract family; the combined checkpoint is retained only as a
research control. Catastrophic forgetting is neither proven nor disproven
here. (Root cause: grouped/objective-level split policy must be frozen
BEFORE any multi-family corpus is assembled — now a mechanical veto for the
training-packet protocol.)

Footgun F7 discovered en route: needle's trainer computes
`total_steps = packed_rows // batch × epochs`; when `batch > packed rows` it
silently runs a **0-step NaN schedule** and "trains" for the full wall-clock,
then promotes the unchanged base model as `_best.pkl` (observation_typing run
1: 782 s of nothing, exact_match 0.0). The lab driver now estimates packed
rows and clamps batch size.

## Undocumented capability probes

### Retrieval revival — head revives; loses to lexical baselines (headline finding #1, as corrected)

### Few-shot ICL at inference — **tested recipes harm; capability not declared impossible**

Injected k ∈ {0,4,8} verified examples via both channels (tools-JSON smuggling
à la `build_needle_tools_context`, and query-prefix), against base and tuned
checkpoints, on the 48-query OOD set (`evals/needle_lab/icl_probe.py`;
log: `logs/icl-probe-verdict.log`):

| Checkpoint / channel | k=0 args | k=4 args | k=8 args | k=8 name |
|---|---|---|---|---|
| tuned / tools | **45.8%** | 33.3% | 18.8% | 39.6% (collapse) |
| tuned / query | **45.8%** | 25.0% | 25.0% | 100% |
| base / either | 0% | 0% | 0% | 33.3% (tools) / 79.2% (query) |

Monotonic args-accuracy degradation in every tuned condition tested (the
tuned/query channel plateaus at 25% from k=4; base-checkpoint tool-NAME
accuracy is non-monotonic in the query channel — 89.6% → 97.9% → 79.2% —
and monotonically collapses only in the tools channel). At k=8 in the tools channel
the examples crowd the real tool schemas out of the 1024-token encoder and
even tool-NAME accuracy collapses. **Scope (audit-corrected): this tests two
particular random-injection recipes at k∈{4,8} on one 48-row routing set —
not the exact production projection, not retrieval-selected shots, not
repeated shot seeds, not k<4.** Actionable conclusion: disable the current
examples-into-inference-context recipe (it is harmful as implemented); do not
declare the capability impossible. The "real-time learning" tier remains
honest as (a) minutes-scale retraining and (b) shadow-ranker retrieval; the
verified-example JSONL channel feeds training.

### Out-of-template generalization (route_selection vs Grok-authored OOD)

100% in-template → **45.8% exact** on 48 messy realistic queries
(parse 100%, name 100%, key 100% — every failure is value-tier; log:
`logs/eval-route-ood-45p8-exact.log`). Failures are
systematic, not noise: long/discursive **coding** requests misroute to
`reasoning/planning` (the template style leaked into the decision boundary);
research/vision/planning classes mostly hold. Lesson: **phrasing diversity
beats example count** — the flywheel must train on verified *real* episodes,
not synthetic templates. (0.48 s/call batched eval on CPU.)

### Self-loop: Needle-ReAct micro-loop on unseen vocabulary — 5/7, mechanism proven

Simulated-env loop (`evals/needle_lab/selfloop.py`) against the next_step
fine-tune, on symbols/files absent from ALL training data, bounds: 8
iterations, same-call-twice stop. (Session-observed: `selfloop.py` printed
its verdict to stdout only and no log file was written — see
`logs/README.md`.) MECHANISM-ONLY evidence (the trained `done` emission
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

Ran against the clean tool_selection checkpoint
(`evals/needle_lab/drift_probe.py`; log: `logs/drift-probe-verdict.log`).
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

## Dry run: manual adaptive-recipe pilot (audit-corrected framing)

Two-iteration pilot of the proposed confusion-driven recipe loop, on the one
*measured* confusion cell (messy-coding→planning misroutes). All arms trained
from the immutable base; candidate records with base/dataset hashes in
`data/arm_candidates.json` (`evals/needle_lab/build_arms.py`). Arm E's
record was originally missing — a defect the audit pass repaired: the
record was reconstructed post-hoc after verifying `route_arm_E.jsonl`
byte-exact as `route_arm_B.jsonl` + the `grok_route_test.py` transform of
`vision_research_delta.json` (dataset sha256[:16] `c2cf0abf9b75ada9`). **Naming correction: the 40-query set
(`sha 52276cf8…`) was frozen with a zero-overlap guard before iteration 1,
but iteration 1's results were used to design arm E, which was then evaluated
on the same set — so from iteration 2 onward it is an *adaptive OOD
development set*, NOT a sealed set.**

| arm (recipe) | adaptive OOD dev | secondary OOD | in-template | worst class | retention* |
|---|---|---|---|---|---|
| A control (templates) | 40.0% | 45.8% | 100% | vision 10% | 3/3 |
| B +40 hard negatives | 45.0% | 47.9% | 100% | vision 10% | 3/3 |
| C +30 metamorphic | 42.5% | 47.9% | 100% | planning 20% | 3/3 |
| D balanced resample | 40.0% | 45.8% | 100% | research 10% | 3/3 |
| E = B + worst-cell delta (iter 2) | 52.5% | 56.2% | 100% | research 20% | 3/3 |

\* retention = three canned tool-calling probes, not a capability suite.

Findings (honest scope):
1. **Small targeted deltas do NOT fix distribution problems** (B/C/D within
   the ±8pp noise band of n=40). OOD movement requires breadth/volume of
   realistic data; the flywheel's fuel must be verified real episodes.
2. **Adaptive-recipe signal, not proof**: iteration 2's worst-cell arm raised
   the development-set score by five additional correct answers (40→52.5%,
   single seed, overlapping confidence intervals) and fixed the vision cell.
   Encouraging directional evidence that worst-cell-targeted data helps;
   NOT a sealed-set improvement claim.
3. **Early stopping matters**: arm B at the naive step target burned 44 min;
   arms C/D/E at 12 fixed epochs (~8–9 min) reached equivalent in-template
   scores on the eval_arms set (arm E's own 10-row trainer split scored
   8/10 — `logs/ft-arm-E-worstcell-12epochs-493s.log`) — but actual
   validation-based early stopping was NOT implemented.
4. **What this pilot is**: pipeline feasibility. The evaluator prints a table
   but computes no Pareto frontier; retention is three canned calls; no
   validation early-stop; single seed. The real optimizer, verifiers, and
   promotion gates remain to be built (per the C0-C7 structural plan).
5. **Caveats**: same-generator correlation (grok-4.5 authored both the
   40-query set and the arm deltas in separate context-free calls; stylistic
   correlation possible — production must diversify generators as it does
   judges).

Verdict for the Swarm integration: **feasibility demonstrated; effectiveness
unproven.** Data-recipe arms remain the most promising search dimension, and
training-vitals vetoes (F7 class), grouped-split enforcement, sealed-set
discipline (with a set that STAYS sealed), multi-seed, and a real Pareto/
forgetting gate are all mandatory before the `training_experiment` target
type carries any authority.

## Design implications (vs the authority-inversion decision log)

| Decision | Lab verdict |
|---|---|
| D5 capsules-as-DB, SQL demoted to projection | **File-only lifecycle demonstrated**; the learned retrieval head loses to char-TF-IDF (60% vs 92%) — deterministic lexical retrieval remains the projection of record, learned head is a shadow-ranker candidate |
| D9 memory-rank cut from v1 families | Revised only to: revived head may run as an additional **shadow** ranker (cheap, side-effect-free); it does not displace deterministic retrieval |
| D4 ICL as falsifiable hypothesis | Tested recipes harm (disable them); capability not declared impossible — retest with production projection, retrieval-selected shots, k<4, repeated seeds |
| D12 never pickle | **CONFIRMED + urgent** (F6) |
| D3 bounded self-loop | Toy-mechanism signal only: sequencing + slot copying on 5 fixed-shape happy paths; no effect/TTL/authority/restart validation; both recovery cases failed |
| D11 catalog-hash pinning | Narrow support (10 renamed-key + 6 new-tool cases); encouraging, not general robustness |

## Reproducibility pins

Upstream needle commit `ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2`; base
weights `needle.pkl` sha256-prefix `40a32e91d1d4197b` (HF
Cactus-Compute/needle, force-download noted in F4); python 3.12.13, jax
0.10.2 (XLA CPU), flax 0.12.7; Apple M3 Max / Darwin 25.5.0.

Lineage: `evals/needle_lab/data/manifest.json` (rebuilt by
`rebuild_manifest.py`) records repo-relative paths, per-file sha256 of the
committed bytes, row counts, and generator provenance for every committed
data file; the 7 generated families additionally carry their tool-catalog
hashes (schema hashes, not content hashes). Retrain-arm records (base +
dataset content hashes, arms B–E) live in `arm_candidates.json`. The
40-query adaptive-dev set is pinned at sha256[:16] `52276cf8e483738b`
(`route_sealed.jsonl`; byte-reproducible from `sealed_raw.json` via
`grok_route_test.py`). `next_step.jsonl` is canonical as committed
(sha256[:16] `8bb294f2c14bb195`): its generator originally chose failure
branches with Python's per-process-randomized `hash()` — fixed post-audit
to a sha256 digest, so the committed bytes, not a re-run, are the record.
Splits: needle `_per_tool_split` seed 42 throughout.

Sanitized evidence bundle (scripts, datasets, manifests, trimmed logs — NO
pickle checkpoints) committed at `evals/needle_lab/`, quarantine-labeled
`research_dev`. Trimmed logs live under `evals/needle_lab/logs/` with a
citation map in `logs/README.md`; two run-1 logs (the F3 matrix crash and
the F7 observation_typing NaN run) were overwritten in-session and are not
recoverable. Full raw lab (28 GB incl. checkpoints and venvs) lives in the
ephemeral session scratchpad only.

## Conclusion (audit-amended)

What one day of empirical work established, and what it did not:

**Supported by evidence**: Needle fine-tunes quickly onto narrow structured
output contracts (100% in-template across families, 0-11% base); selection,
id-copying, schema-conditioned calls, and value transfer are promising reflex
behaviors; schema-reading generalization selects never-trained tools from
descriptions; the released contrastive head is structurally dead and a
re-initialized replacement can learn a small ranking problem; a bounded loop
can sequence multi-step happy paths on unseen vocabulary in a toy simulator;
the file-only artifact lifecycle works; the engineering footguns (F1-F7) are
real and reproducible.

**Not established** (removed or corrected after independent audit): the
zero-interference claim (invalid — split contamination); the "+12.5pp sealed"
claim (adaptive development set, single seed, 5 answers); the SQLite/
retrieval-replacement claim (char-TF-IDF 92% beats the head's 60%); the
blanket no-ICL claim (two recipes tested); general drift robustness; any
promotion-grade evidence of the synapse-reflex architecture.

**Standing lessons**: template accuracy is not capability (40-56% OOD);
diverse verified real episodes are the fuel; recovery needs complete repair
arcs; production training separates by exact output-contract family (the
combined checkpoint is a research control only); grouped-split policy,
Pareto/forgetting gates, multi-seed, and validation early-stop must be
frozen BEFORE the next training campaign; and a model judge cannot establish
success — during this very lab the CLI-pinned reviewer twice returned a
promise labeled `finish_reason=final_answer`.

**Status**: all datasets and checkpoints from this lab are quarantined
`research_dev` — mechanism evidence and baseline arms, never verified
training truth, validation, or sealed evaluation. Next: Codex freezes the
exact Needle contracts, TTL representation, envelopes, grouped-split policy,
and verifiers (C0-C7); then the Gemini synthetic campaign starting at the
2,000-root pilot gated on schema/novelty/leakage/verifier-yield; the system
benchmark compares complete systems (code floor / +Needle / +Gemma /
fast-Grok±Needle / Grok 4.5 / Grok Build) on downstream verified objective
success with TTL, authority, effects, receipts, recovery, latency, multi-seed.

---
*Evidence bundle: `evals/needle_lab/` (research_dev). CI note: the full
suite runs green at this head (1,234 tests locally under CI's invocation,
`pytest tests/` on the branch rebased onto main). This PR is not purely
docs: committing the evidence bundle exposed a flat-alphabetical-ordering
weakness in the bounded `list_project_files` listing (deep bundle files
crowded out `src/` and `pyproject.toml`), fixed in `src/tools/system.py`
with shallow-first ordering plus a regression test. Green CI validates the
tree and that one fix — not the experiments. Agent-Assisted-By: Claude via
Claude Code; peer review: Grok 4.5 (CLI plane), ChatGPT 5.6 Sol (relayed,
incl. the audit that corrected this report), Gemini/Antigravity (structural
plan).*
