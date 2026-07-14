export const meta = {
  name: 'needle-training-campaign',
  description: 'Confusion-driven Needle training campaign with mechanical gates (mock-first; live requires a Codex-approved training manifest)',
  whenToUse: 'Run the Needle training-experiment loop from an immutable training packet: corpus vetoes, arm lanes, Pareto/retention gates, evidence bundle. Default mode=mock replays committed research_dev artifacts and trains nothing.',
  phases: [
    { title: 'Preflight', detail: 'authorization, packet hashes, frozen split policy, env pins' },
    { title: 'Corpus veto', detail: 'grouped-split, leakage, dedup, balance — hard gates' },
    { title: 'Arm lanes', detail: 'per-arm train+eval+retention (mock: replay committed artifacts)' },
    { title: 'Cross-arm gate', detail: 'trivial baselines, Pareto/forgetting, multi-seed rules' },
    { title: 'Evidence', detail: 'manifest regen, report draft, adversarial claim verification' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// AUTHORIZATION CONTRACT (mirrors .agents/campaigns/gemma-needle-2000-v1)
//
// mode=mock (default): transport-free discipline. NO training run, NO dataset
//   generation, NO sealed evaluation. Inputs are the committed research_dev
//   bundle (evals/needle_lab/); arm records are REPLAYED from
//   data/arm_candidates.json + logs, never invented. Purpose: prove the
//   orchestration, veto, gate, ledger, and evidence contracts end-to-end —
//   the same pattern as the campaign's stage1 mock safety gate.
//
// mode=live: requires args.manifest — a Codex-approved training manifest
//   (exact-head approval, separate PR) with explicit bounds:
//   { training_enabled: true, packet: <immutable packet path OUTSIDE
//     research_dev>, max_lanes, seeds, max_iterations, wall_budget_min,
//     sealed_evaluation_enabled }. The script throws before any work if the
//   manifest is missing or does not authorize training. The sealed set path
//   is never given to any agent except the single final sealed evaluation,
//   and only when sealed_evaluation_enabled === true.
//
// The script is the mechanical authority: agents diagnose and draft; only
// this control flow decides pass/fail, and every gate rule is code below.
// ─────────────────────────────────────────────────────────────────────────────

// Tolerate args arriving JSON-encoded as a string (observed in practice).
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const MODE = A.mode || 'mock'
const STOP_AFTER = A.stop_after || 'full' // preflight|veto|lanes|gate|full
const RUN_STAMP = A.run_stamp || 'unstamped-run' // caller supplies; no Date.now in workflows
const MAX_ITER = A.max_iterations || 1

let manifest = null
if (MODE === 'live') {
  manifest = A.manifest
  if (!manifest || manifest.training_enabled !== true || !manifest.packet) {
    throw new Error(
      'live mode refused: a Codex-approved training manifest with ' +
      'training_enabled=true and an immutable packet path is required. ' +
      'Run mode=mock to exercise the contracts.'
    )
  }
  if (String(manifest.packet).includes('needle_lab')) {
    throw new Error('live mode refused: research_dev quarantine data can never be a live training packet.')
  }
} else if (MODE !== 'mock') {
  throw new Error(`unknown mode "${MODE}" — use mock or live`)
}

// Mock packet must be a research_dev needle_lab bundle (default: the one in
// this checkout; override with args.packet when the bundle lives in another
// worktree, e.g. before PR #64 lands).
const PACKET = MODE === 'mock' ? (A.packet || 'evals/needle_lab') : manifest.packet
if (MODE === 'mock' && !String(PACKET).includes('needle_lab')) {
  throw new Error('mock mode refused: packet must be a research_dev needle_lab bundle.')
}
const SEEDS = MODE === 'mock' ? [42] : (manifest.seeds || [42, 43, 44])
const LEDGER = [] // attempt-ledger discipline: one row per agent call

const GATE_SCHEMA = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' },
    violations: { type: 'array', items: { type: 'string' } },
    facts: { type: 'string' },
  },
  required: ['pass', 'violations', 'facts'],
}

const LANE_SCHEMA = {
  type: 'object',
  properties: {
    arm: { type: 'string' },
    seed: { type: 'integer' },
    trained: { type: 'boolean' },
    vitals_veto: { type: 'string', description: 'ok | the exact F7-class violation observed' },
    in_template: { type: 'number' },
    dev_ood: { type: 'number' },
    secondary_ood: { type: 'number' },
    worst_class: { type: 'string' },
    retention: { type: 'string' },
    wall_seconds: { type: 'number' },
    evidence_paths: { type: 'array', items: { type: 'string' } },
    facts: { type: 'string' },
  },
  required: ['arm', 'seed', 'trained', 'vitals_veto', 'in_template', 'dev_ood', 'retention', 'evidence_paths', 'facts'],
}

const BASELINE_SCHEMA = {
  type: 'object',
  properties: {
    baselines: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          metric: { type: 'string' },
          score: { type: 'number' },
        },
        required: ['name', 'metric', 'score'],
      },
    },
    facts: { type: 'string' },
  },
  required: ['baselines', 'facts'],
}

const CLAIMCHECK_SCHEMA = {
  type: 'object',
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string' },
          artifact: { type: 'string' },
          verdict: { type: 'string', enum: ['CONFIRMED', 'REFUTED', 'NO_ARTIFACT'] },
        },
        required: ['claim', 'artifact', 'verdict'],
      },
    },
    facts: { type: 'string' },
  },
  required: ['claims', 'facts'],
}

const COMMON = `You are one stage of the needle-training-campaign workflow (run ${RUN_STAMP}, mode=${MODE}) in the uni-grok-mcp repository (work from the repository root of the current session). The workflow script — not you — decides pass/fail; your job is to run EXACT commands, read ONLY the files named in your task, and return precisely the structured result requested. Never touch, cite, or read any sealed evaluation set. Never generate training examples. Report measured values only; if something cannot be verified, say so in a violation/facts entry instead of guessing.`

async function call(label, phase, prompt, schema, effort) {
  const res = await agent(prompt, { label, phase, schema, effort: effort || 'low' })
  LEDGER.push({ phase, label, ok: res !== null })
  if (res === null) throw new Error(`agent ${label} returned null — treat as gate failure, not success`)
  return res
}

// ── Phase 1: Preflight ───────────────────────────────────────────────────────
phase('Preflight')
const preflight = await call('preflight', 'Preflight', `${COMMON}

Verify the training packet and environment WITHOUT modifying anything:
1. Packet ${PACKET}: data manifest exists (data/manifest.json for mock; packet manifest for live) and EVERY listed sha256 matches the file bytes on disk (shasum -a 256). List every mismatch or unlisted data file as a violation.
2. Split policy: confirm the split convention is recorded BEFORE corpus assembly (mock: needle _per_tool_split seed 42 pinned in the packet README/report; live: manifest.split_policy digest matches the packet). Violation if absent.
3. Base checkpoint pin present (mock: sha256 prefix 40a32e91d1d4197b recorded in README). Do NOT download anything in mock mode.
4. Env pins recorded (python/jax/flax versions). Violation only if unrecorded, not if the local env differs — record the difference in facts.
Return pass=false if ANY violation.`, GATE_SCHEMA)

if (!preflight.pass) return { verdict: 'REFUSED_PREFLIGHT', mode: MODE, violations: preflight.violations, ledger: LEDGER }
if (STOP_AFTER === 'preflight') return { verdict: 'STOPPED_AFTER_PREFLIGHT', mode: MODE, preflight, ledger: LEDGER }

// ── Phase 2: Corpus veto (hard gates — any violation kills the run) ─────────
phase('Corpus veto')
const veto = await call('corpus-veto', 'Corpus veto', `${COMMON}

Run the mechanical corpus vetoes on ${PACKET}/data (READ-ONLY):
1. Grouped-split/leakage: run \`python ${PACKET}/check_combined_contamination.py\` (or the packet's equivalent) and report the counts. In mock mode the committed combined.jsonl is a KNOWN-CONTAMINATED research control: expect nonzero counts and record them as facts, with the single violation string "combined.jsonl contaminated (known research control — excluded from any training corpus)". In live mode ANY nonzero count is a fatal violation.
2. Exact-duplicate scan across every family JSONL (normalize each row to (query, answers); report duplicate counts per family). Known-voided families (mock: abstention 20/40, next_step prefix leakage) are recorded as violations with the marker "(known, quarantined)".
3. Class balance per enum family (report counts; imbalance is a fact, not a violation).
4. Secret/PII grep over all data files (any hit is fatal).
pass=false only for NEW violations — i.e. anything not carrying a "(known" marker in mock mode; in live mode every violation is fatal.`, GATE_SCHEMA, 'medium')

const newViolations = veto.violations.filter(v => MODE === 'live' || !v.includes('(known'))
if (!veto.pass || newViolations.length > 0) {
  return { verdict: 'REFUSED_CORPUS_VETO', mode: MODE, violations: veto.violations, ledger: LEDGER }
}
if (STOP_AFTER === 'veto') return { verdict: 'STOPPED_AFTER_VETO', mode: MODE, preflight, veto, ledger: LEDGER }

// ── Phase 3+4: iteration loop — arm lanes, then cross-arm gate ──────────────
const iterations = []
let arms
for (let iter = 1; iter <= MAX_ITER; iter++) {
  phase('Arm lanes')
  if (MODE === 'mock') {
    // Replay ONLY committed candidate records — never invent data in mock.
    const rec = await call('arm-records', 'Arm lanes', `${COMMON}

Read ${PACKET}/data/arm_candidates.json and return facts as a compact JSON string of the array [{arm, dataset_hash, n, recipe}] — every committed record, nothing added. pass=true unless the file is missing/unparseable.`, GATE_SCHEMA)
    arms = JSON.parse(rec.facts)
  } else {
    // Live arm design happens ONLY under the Codex manifest's bounds and is
    // deliberately not implemented until that manifest exists. Fail loudly.
    throw new Error('live arm design is blocked until a Codex-approved training manifest defines its bounds (confusion-cell inputs, generator diversity rules, per-arm budgets).')
  }

  const laneInputs = arms.flatMap(a => SEEDS.map(seed => ({ ...a, seed })))
  const lanes = (await pipeline(
    laneInputs,
    lane => call(`lane:${lane.arm}/s${lane.seed}`, 'Arm lanes', `${COMMON}

Arm lane ${lane.arm} seed ${lane.seed} (${MODE}).
MOCK: do not train. Reconstruct this lane's result from committed artifacts ONLY: ${PACKET}/data/arm_results.json and the matching ${PACKET}/logs/ft-arm-*.log. NAMING: arm_candidates.json uses "arm_B/arm_C/arm_D/arm_E"; arm_results.json names the SAME arms "B_hardneg/C_metamorphic/D_balanced/E_worstcell" and the control "A_control" — match by the letter. FIELD MAP (legacy keys): results key "sealed" -> report as dev_ood (it is the adaptive OOD dev set); results key "dev_ood" -> report as secondary_ood; "in_template" as-is; "forgetting" -> retention. From the log take wall seconds and run the F7 vitals check (Total steps > 0, training completed, no NaN schedule) -> vitals_veto "ok" or the violation. trained=false. List the exact evidence paths used. Every arm B-E has a committed result row and log; return in_template=-1 ONLY if a read genuinely fails, and say why in facts.
LIVE (never reached without a manifest): run the packet's ft driver wrapped with the vitals veto, then eval + retention.`, LANE_SCHEMA)
  )).filter(Boolean)

  if (STOP_AFTER === 'lanes' && iter === MAX_ITER) {
    return { verdict: 'STOPPED_AFTER_LANES', mode: MODE, arms, lanes, ledger: LEDGER }
  }

  // ── Cross-arm gate: needs ALL lanes together (legitimate barrier) ─────────
  phase('Cross-arm gate')
  const baselines = await call('trivial-baselines', 'Cross-arm gate', `${COMMON}

Run the trivial-baseline comparison that every learned candidate must beat (audit standing rule). Mock: run \`python ${PACKET}/tfidf_baseline.py\` and return each printed variant as {name, metric:"top1", score in [0,1]}. Also read the control arm A_control from ${PACKET}/data/arm_results.json and return {name:"control-arm-A", metric:"dev_ood", score:<A_control's legacy "sealed" key — the adaptive OOD dev metric, the SAME field lanes report as dev_ood>}.`, BASELINE_SCHEMA)

  // Mechanical gate rules — CODE, not agent judgment:
  const controlDev = (baselines.baselines.find(b => b.name === 'control-arm-A') || { score: 0 }).score
  const judged = arms.map(a => {
    const armLanes = lanes.filter(l => l.arm === a.arm && l.in_template >= 0)
    const seedsRun = new Set(armLanes.map(l => l.seed)).size
    const vitalsOk = armLanes.length > 0 && armLanes.every(l => l.vitals_veto === 'ok')
    const retentionOk = armLanes.every(l => /^3\/3$|^full$/i.test(l.retention))
    const devScores = armLanes.map(l => l.dev_ood)
    const minDev = devScores.length ? Math.min(...devScores) : -1
    const beatsControl = minDev > controlDev
    const multiSeedOk = seedsRun >= (MODE === 'mock' ? 1 : 3)
    return {
      arm: a.arm,
      seeds_run: seedsRun,
      vitals_ok: vitalsOk,
      retention_ok: retentionOk,
      min_dev_ood: minDev,
      beats_control: beatsControl,
      multi_seed_ok: multiSeedOk,
      // Promotion-candidate rule: every mechanical gate green. In mock,
      // multi-seed is structurally impossible (1 historical seed) so no arm
      // can be promotion-grade — which is exactly the audited conclusion.
      promotion_candidate: vitalsOk && retentionOk && beatsControl && multiSeedOk && MODE === 'live',
    }
  })
  iterations.push({ iter, arms: judged, baselines: baselines.baselines })

  if (STOP_AFTER === 'gate' && iter === MAX_ITER) break
  // Iterate only when live, authorized, and something is still improving.
  if (MODE === 'mock' || iter === MAX_ITER) break
  if (!judged.some(j => j.beats_control)) break
}

if (STOP_AFTER === 'gate') {
  return { verdict: 'STOPPED_AFTER_GATE', mode: MODE, iterations, ledger: LEDGER }
}

// ── Phase 5: Evidence — draft, then adversarially verify every claim ────────
phase('Evidence')
const last = iterations[iterations.length - 1]
const draft = await call('report-draft', 'Evidence', `${COMMON}

Draft (in facts, as markdown) a concise campaign result summary for run ${RUN_STAMP}: mode; gate outcomes from PREFLIGHT ${JSON.stringify({ pass: preflight.pass, violations: preflight.violations })} and CORPUS VETO ${JSON.stringify({ pass: veto.pass, violations: veto.violations })}; per-arm table from this JSON: ${JSON.stringify(last.arms)}; baselines: ${JSON.stringify(last.baselines)}. State plainly that mock mode trains nothing and that promotion authority belongs to the Codex gate. Every sentence must restate the JSON above or cite a committed artifact by path — no claims about your own process or about anything not in the JSON.`, GATE_SCHEMA)

const check = await call('claim-verify', 'Evidence', `${COMMON}

Adversarially verify the draft below. For EVERY numeric or factual claim, name the committed artifact (file path) that backs it and verdict CONFIRMED, or REFUTED (artifact contradicts), or NO_ARTIFACT (nothing backs it). A claim sourced only from the workflow's own JSON is CONFIRMED with artifact "workflow-state" ONLY if it matches that JSON exactly.

DRAFT:
${draft.facts}`, CLAIMCHECK_SCHEMA, 'medium')

const unbacked = check.claims.filter(c => c.verdict !== 'CONFIRMED')
return {
  verdict: unbacked.length === 0 ? 'EVIDENCE_CLEAN' : 'EVIDENCE_GAPS',
  mode: MODE,
  run: RUN_STAMP,
  promotion: 'NONE — candidates + evidence only; promotion belongs to the Codex landing gate',
  iterations,
  report_draft: draft.facts,
  claim_check: { unbacked, total: check.claims.length },
  ledger: LEDGER,
}
