export const meta = {
  name: 'needle-training-campaign',
  description: 'Confusion-driven Needle training campaign with mechanical gates (mock-first; live requires a Codex-approved training manifest)',
  whenToUse: 'Run the Needle training-experiment loop from an immutable training packet: corpus vetoes, arm lanes, Pareto/retention gates, evidence bundle. Default mode=mock replays committed research_dev artifacts and trains nothing.',
  phases: [
    { title: 'Preflight', detail: 'digest-sealed validator receipt: packet hashes, frozen split policy, env pins' },
    { title: 'Corpus veto', detail: 'digest-sealed validator receipt: leakage, dedup, balance, secrets — hard gates' },
    { title: 'Arm lanes', detail: 'frozen arm records + mechanical lane vitals from validator receipts (mock: replay)' },
    { title: 'Cross-arm gate', detail: 'control comparison, retention, multi-seed rules — computed here from verified payloads' },
    { title: 'Evidence', detail: 'report draft, adversarial claim verification, typed next_harvest_request (request-only)' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// AUTHORIZATION CONTRACT (mirrors .agents/campaigns/gemma-needle-2000-v1)
//
// mode=mock (default): transport-free discipline. NO training run, NO dataset
//   generation, NO sealed evaluation. Inputs are the committed research_dev
//   bundle (evals/needle_lab/); arm records are REPLAYED from
//   data/arm_candidates.json + logs, never invented.
//
// mode=live: requires args.manifest — a Codex-approved training manifest
//   (exact-head approval, separate PR) with explicit bounds:
//   { training_enabled: true, packet: <immutable packet path OUTSIDE
//     research_dev>, max_lanes, seeds, max_iterations, wall_budget_min,
//     sealed_evaluation_enabled, receipt_digest_pins: { preflight, corpus_veto,
//     arm_records } }. The script throws before any work if the manifest is
//   missing or does not authorize training. The sealed set path is never given
//   to any agent except the single final sealed evaluation, and only when
//   sealed_evaluation_enabled === true.
//
// MECHANICAL AUTHORITY — HOW GATE TRUTH IS ESTABLISHED
//
// Gate decisions are made by deterministic validators that live in executable
// repository code: `evals/needle_gates` (Python, tested by
// tests/test_needle_gates.py). Each validator emits a digest-sealed receipt:
//
//   { schema: "needle-gate-receipt/v1", validator, payload_b64,
//     payload_sha256 }
//
// where payload_sha256 = SHA-256 over the exact base64-decoded payload bytes.
// Agents in this workflow only RUN the validator CLI and TRANSCRIBE the
// receipt back; they return no pass booleans and no measurements of their
// own. This script base64-decodes the payload, recomputes SHA-256 with the
// embedded implementation below, and derives every gate decision from the
// verified typed payload. Any decode/digest/schema mismatch throws — fail
// closed, never fall back to agent prose.
//
// Because this workflow runtime cannot execute the Python validators
// directly, transcription alone cannot prove the receipt was produced by the
// committed code. Two layers close that gap:
//   • mock: receipts are written to disk (--out) so any reviewer can rerun
//     `python -m evals.needle_gates verify --receipt <file> --expect-digest`
//     and rerun the validators; the digest in this run's result binds the
//     audit trail. Mock trains nothing, so the residual risk is bounded.
//   • live: the Codex-approved manifest MUST pin the expected
//     payload_sha256 for each gate receipt (receipt_digest_pins), computed
//     by running the validators out-of-band during approval. A presented
//     receipt whose digest does not match its pin throws. No pins → refuse
//     to start. Agent prose can therefore never become gate truth.
// ─────────────────────────────────────────────────────────────────────────────

// Tolerate args arriving JSON-encoded as a string (observed in practice).
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const MODE = A.mode || 'mock'
const STOP_AFTER = A.stop_after || 'full' // preflight|veto|lanes|gate|full
const MAX_ITER = A.max_iterations || 1

// ── Strict validation of every value embedded in validator commands ─────────
// These values are interpolated into the exact CLI commands agents run. A
// hostile value must never smuggle shell syntax, whitespace, option injection
// or path traversal into a command string — validate BEFORE any command is
// built or any agent is called. Mirrors evals/needle_gates/identifiers.py.
const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const SAFE_PATH = /^\/?[A-Za-z0-9._][A-Za-z0-9._/-]*$/
function safeIdentifier(name, value) {
  if (typeof value !== 'string' || !SAFE_IDENTIFIER.test(value)) {
    throw new Error(`${name} ${JSON.stringify(value)} rejected: must be a single [A-Za-z0-9._-] token (no shell syntax, no whitespace, no leading '-')`)
  }
  return value
}
function safePacketPath(value) {
  if (typeof value !== 'string' || !SAFE_PATH.test(value)) {
    throw new Error(`packet path ${JSON.stringify(value)} rejected: no shell syntax, no whitespace, no leading '-'`)
  }
  if (value.split('/').some(part => part === '..')) {
    throw new Error(`packet path ${JSON.stringify(value)} rejected: '..' traversal is not allowed`)
  }
  return value
}

const RUN_STAMP = safeIdentifier('run_stamp', A.run_stamp || 'unstamped-run') // caller supplies; no Date.now in workflows
const CAMPAIGN_ID = safeIdentifier('campaign_id', A.campaign_id || 'needle-campaign')
const SOURCE_DATASET = safeIdentifier('source_dataset_id', A.source_dataset_id || 'D0001')
const TARGET_DATASET = safeIdentifier('target_dataset_id', A.target_dataset_id || 'D0002')

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
  if (String(manifest.packet).includes('needle_lab') || String(manifest.packet).includes('mock_packet')) {
    throw new Error('live mode refused: research_dev quarantine data and committed mock fixtures can never be a live training packet.')
  }
  const pins = manifest.receipt_digest_pins
  if (!pins || !pins.preflight || !pins.corpus_veto || !pins.arm_records) {
    throw new Error(
      'live mode refused: manifest.receipt_digest_pins must pin the expected ' +
      'payload_sha256 for preflight, corpus_veto, and arm_records receipts ' +
      '(computed by running evals.needle_gates during Codex approval). ' +
      'Without pins this runtime cannot verify validator provenance — fail closed.'
    )
  }
} else if (MODE !== 'mock') {
  throw new Error(`unknown mode "${MODE}" — use mock or live`)
}

// Mock packet default: the tiny committed fixture (present on main, validated
// by tests/test_needle_gates.py). Override with args.packet to point at a
// research_dev needle_lab bundle in another worktree — never anything else.
const MOCK_FIXTURE_PACKET = 'evals/needle_gates/fixtures/mock_packet'
const PACKET = safePacketPath(MODE === 'mock' ? (A.packet || MOCK_FIXTURE_PACKET) : String(manifest.packet))
if (MODE === 'mock' && PACKET !== MOCK_FIXTURE_PACKET && !PACKET.includes('needle_lab')) {
  throw new Error('mock mode refused: packet must be the committed mock fixture or a research_dev needle_lab bundle.')
}
const SEEDS = MODE === 'mock' ? [42] : (manifest.seeds || [42, 43, 44])
const RECEIPTS_DIR = `/tmp/needle-gates-${RUN_STAMP}`
const LEDGER = [] // attempt-ledger discipline: one row per agent call

// ── Embedded receipt verification (base64 + SHA-256, no runtime deps) ───────
// Receipts payloads are canonical ASCII JSON, so byte handling is exact.

const B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
function b64decode(s) {
  const clean = String(s).replace(/=+$/, '')
  if (/[^A-Za-z0-9+/]/.test(clean)) throw new Error('receipt payload_b64 contains non-base64 characters')
  const bytes = []
  let buffer = 0
  let bits = 0
  for (const ch of clean) {
    buffer = (buffer << 6) | B64_ALPHABET.indexOf(ch)
    bits += 6
    if (bits >= 8) {
      bits -= 8
      bytes.push((buffer >> bits) & 0xff)
    }
  }
  return bytes
}

const SHA256_K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]
function rotr(x, n) { return ((x >>> n) | (x << (32 - n))) >>> 0 }
function sha256Hex(bytes) {
  const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
  const msg = bytes.slice()
  const bitLenHi = Math.floor(bytes.length / 0x20000000)
  const bitLenLo = (bytes.length << 3) >>> 0
  msg.push(0x80)
  while (msg.length % 64 !== 56) msg.push(0)
  for (let i = 3; i >= 0; i--) msg.push((bitLenHi >>> (8 * i)) & 0xff)
  for (let i = 3; i >= 0; i--) msg.push((bitLenLo >>> (8 * i)) & 0xff)
  const w = new Array(64)
  for (let off = 0; off < msg.length; off += 64) {
    for (let i = 0; i < 16; i++) {
      w[i] = ((msg[off + 4 * i] << 24) | (msg[off + 4 * i + 1] << 16) | (msg[off + 4 * i + 2] << 8) | msg[off + 4 * i + 3]) >>> 0
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0
    }
    let [a, b, c, d, e, f, g, hh] = h
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
      const ch = (e & f) ^ (~e & g)
      const t1 = (hh + S1 + ch + SHA256_K[i] + w[i]) >>> 0
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (S0 + maj) >>> 0
      hh = g; g = f; f = e
      e = (d + t1) >>> 0
      d = c; c = b; b = a
      a = (t1 + t2) >>> 0
    }
    h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0
    h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0; h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0
  }
  return h.map(x => x.toString(16).padStart(8, '0')).join('')
}

// Verify a transcribed receipt envelope and return its typed payload.
// Throws on ANY mismatch — fail closed; agent prose never becomes gate truth.
function verifyReceipt(rawText, expectedValidator) {
  let receipt
  try {
    receipt = typeof rawText === 'string' ? JSON.parse(rawText) : rawText
  } catch (e) {
    throw new Error(`gate failure: ${expectedValidator} receipt is not JSON (${e.message})`)
  }
  if (!receipt || receipt.schema !== 'needle-gate-receipt/v1') {
    throw new Error(`gate failure: ${expectedValidator} receipt has wrong schema ${receipt && receipt.schema}`)
  }
  if (receipt.validator !== expectedValidator) {
    throw new Error(`gate failure: receipt validator ${receipt.validator} != expected ${expectedValidator}`)
  }
  if (typeof receipt.payload_b64 !== 'string' || typeof receipt.payload_sha256 !== 'string') {
    throw new Error(`gate failure: ${expectedValidator} receipt missing payload_b64/payload_sha256`)
  }
  const payloadBytes = b64decode(receipt.payload_b64)
  const digest = sha256Hex(payloadBytes)
  if (digest !== receipt.payload_sha256) {
    throw new Error(`gate failure: ${expectedValidator} receipt digest mismatch (computed ${digest}, declared ${receipt.payload_sha256})`)
  }
  if (MODE === 'live') {
    const pinned = manifest.receipt_digest_pins[expectedValidator]
    if (pinned && pinned !== digest) {
      throw new Error(`gate failure: ${expectedValidator} receipt digest ${digest} does not match Codex-pinned ${pinned}`)
    }
  }
  let payload
  try {
    payload = JSON.parse(payloadBytes.map(b => String.fromCharCode(b)).join(''))
  } catch (e) {
    throw new Error(`gate failure: ${expectedValidator} receipt payload is not JSON (${e.message})`)
  }
  if (!payload || typeof payload !== 'object') {
    throw new Error(`gate failure: ${expectedValidator} receipt payload is not an object`)
  }
  return { payload, digest }
}

// Agents transcribe receipts; they never return pass booleans or metrics.
const RECEIPT_AGENT_SCHEMA = {
  type: 'object',
  properties: {
    receipt: { type: 'string', description: 'the EXACT receipt JSON emitted by the validator CLI, transcribed byte-for-byte' },
    facts: { type: 'string', description: 'one-line summary of what you ran (advisory only; carries no gate authority)' },
  },
  required: ['receipt', 'facts'],
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

const DRAFT_SCHEMA = {
  type: 'object',
  properties: { facts: { type: 'string' } },
  required: ['facts'],
}

const COMMON = `You are one stage of the needle-training-campaign workflow (run ${RUN_STAMP}, mode=${MODE}) in the uni-grok-mcp repository (work from the repository root of the current session). Deterministic validators in evals/needle_gates — not you, not this prompt — decide every gate; your job is to run EXACT commands and transcribe their receipt output byte-for-byte. Never touch, cite, or read any sealed evaluation set. Never generate training examples. If a command fails, transcribe whatever it printed and say what failed in facts — do not fabricate a receipt.`

async function call(label, phase, prompt, schema, effort) {
  const res = await agent(prompt, { label, phase, schema, effort: effort || 'low' })
  LEDGER.push({ phase, label, ok: res !== null })
  if (res === null) throw new Error(`agent ${label} returned null — treat as gate failure, not success`)
  return res
}

// Run one validator through an agent and return its digest-verified payload.
async function gateReceipt(label, phaseName, validator, cliArgs, effort) {
  const outPath = `${RECEIPTS_DIR}/${validator}.json`
  const res = await call(label, phaseName, `${COMMON}

Run EXACTLY this command from the repository root (read-only on the packet; the receipt goes to /tmp):

  uv run python -m evals.needle_gates ${cliArgs} --out ${outPath}

Then return:
- receipt: the full JSON text printed by the command (identical to ${outPath}), transcribed EXACTLY — no edits, no reformatting, no commentary inside the JSON.
- facts: one line noting the command ran and where the receipt file is.
You do not decide pass/fail; the workflow verifies the receipt digest itself.`, RECEIPT_AGENT_SCHEMA, effort)
  const { payload, digest } = verifyReceipt(res.receipt, validator)
  LEDGER.push({ phase: phaseName, label: `${label}:receipt-verified`, ok: true, payload_sha256: digest })
  return { payload, digest, receipt_path: outPath }
}

// ── Phase 1: Preflight ───────────────────────────────────────────────────────
phase('Preflight')
const preflight = await gateReceipt(
  'preflight', 'Preflight', 'preflight', `preflight --packet ${PACKET}`
)
// Gate truth: the verified typed payload — never the agent's words.
if (preflight.payload.ok !== true || preflight.payload.violations.length > 0) {
  return {
    verdict: 'REFUSED_PREFLIGHT', mode: MODE,
    violations: preflight.payload.violations,
    receipt_sha256: preflight.digest, ledger: LEDGER,
  }
}
if (STOP_AFTER === 'preflight') {
  return { verdict: 'STOPPED_AFTER_PREFLIGHT', mode: MODE, preflight: preflight.payload, receipt_sha256: preflight.digest, ledger: LEDGER }
}

// ── Phase 2: Corpus veto (hard gates — any NEW violation kills the run) ─────
phase('Corpus veto')
const veto = await gateReceipt(
  'corpus-veto', 'Corpus veto', 'corpus_veto', `corpus-veto --packet ${PACKET}`, 'medium'
)
// The validator computes new_violations mechanically (known-quarantine markers
// come from the committed packet manifest, never from prose). Recompute here
// as defense in depth; live mode treats EVERY violation as fatal.
const vetoViolations = veto.payload.violations || []
const newViolations = MODE === 'live'
  ? vetoViolations
  : vetoViolations.filter(v => !v.includes('(known'))
const validatorNew = veto.payload.new_violations || []
if (veto.payload.ok !== true || newViolations.length > 0 || (MODE !== 'live' && validatorNew.length > 0)) {
  return {
    verdict: 'REFUSED_CORPUS_VETO', mode: MODE,
    violations: vetoViolations, new_violations: MODE === 'live' ? vetoViolations : validatorNew,
    receipt_sha256: veto.digest, ledger: LEDGER,
  }
}
if (STOP_AFTER === 'veto') {
  return { verdict: 'STOPPED_AFTER_VETO', mode: MODE, preflight: preflight.payload, veto: veto.payload, ledger: LEDGER }
}

// ── Phase 3+4: iteration loop — arm lanes, then cross-arm gate ──────────────
const iterations = []
let armsPayload
let metricsReceipt
for (let iter = 1; iter <= MAX_ITER; iter++) {
  phase('Arm lanes')
  // Arm records are FROZEN, manifest-declared data replayed through the
  // validator — in every mode. No agent is ever asked to invent an arm or a
  // dataset (they are only asked to run the CLI and transcribe its receipt).
  const armRecords = await gateReceipt(
    'arm-records', 'Arm lanes', 'arm_records', `arm-records --packet ${PACKET}`
  )
  if (armRecords.payload.ok !== true || armRecords.payload.arms.length === 0) {
    return {
      verdict: 'REFUSED_ARM_RECORDS', mode: MODE,
      violations: armRecords.payload.violations,
      receipt_sha256: armRecords.digest, ledger: LEDGER,
    }
  }
  armsPayload = armRecords.payload.arms

  if (MODE === 'live') {
    // Live lane TRAINING stays blocked until a Codex-approved manifest defines
    // its bounds (per-arm budgets, generator diversity, confusion-cell
    // inputs). The frozen arm records above are the only permitted inputs.
    throw new Error('live lane training is blocked until a Codex-approved training manifest defines its bounds (confusion-cell inputs, generator diversity rules, per-arm budgets). Frozen arm records were loaded and verified; nothing was trained.')
  }

  // Mock lanes: one arm-metrics receipt replays committed results and runs
  // the F7 vitals checks (steps > 0, completed, no NaN) mechanically against
  // the committed logs. The legacy field map (results "sealed" -> dev_ood,
  // results "dev_ood" -> secondary_ood, "forgetting" -> retention) lives in
  // the validator, in code.
  metricsReceipt = await gateReceipt(
    'arm-metrics', 'Arm lanes', 'arm_metrics', `arm-metrics --packet ${PACKET}`, 'medium'
  )
  if (metricsReceipt.payload.ok !== true) {
    return {
      verdict: 'REFUSED_ARM_METRICS', mode: MODE,
      violations: metricsReceipt.payload.violations,
      receipt_sha256: metricsReceipt.digest, ledger: LEDGER,
    }
  }

  // Build lane rows from the VERIFIED payload only (mock: replay,
  // trained=false). The validator already bound each frozen arm record to
  // its exact results identity and dataset digest (rejecting duplicates,
  // aliases, and unbound rows) — join here strictly by the exact arm name.
  const metricsByArm = {}
  for (const armMetric of metricsReceipt.payload.arms) {
    metricsByArm[armMetric.arm] = armMetric
  }
  const lanes = []
  for (const record of armsPayload) {
    const metric = metricsByArm[record.arm]
    for (const seed of SEEDS) {
      if (!metric) {
        lanes.push({ arm: record.arm, seed, trained: false, vitals_veto: `no bound results row for ${record.arm}`, in_template: -1, dev_ood: -1, secondary_ood: -1, retention: '', wall_seconds: null })
        continue
      }
      if (metric.results_name !== record.results_name || metric.dataset_hash !== record.dataset_hash) {
        lanes.push({ arm: record.arm, seed, trained: false, vitals_veto: `identity binding mismatch for ${record.arm}: results_name/dataset_hash do not match the frozen record`, in_template: -1, dev_ood: -1, secondary_ood: -1, retention: '', wall_seconds: null })
        continue
      }
      lanes.push({
        arm: record.arm,
        results_name: metric.results_name,
        dataset_hash: metric.dataset_hash,
        seed,
        trained: false,
        vitals_veto: metric.vitals.vitals_veto,
        in_template: metric.in_template,
        dev_ood: metric.dev_ood,
        secondary_ood: metric.secondary_ood,
        retention: metric.retention,
        wall_seconds: metric.vitals.wall_seconds,
        log_sha256: metric.vitals.log_sha256,
      })
    }
  }

  if (STOP_AFTER === 'lanes' && iter === MAX_ITER) {
    return { verdict: 'STOPPED_AFTER_LANES', mode: MODE, arms: armsPayload, lanes, receipt_sha256: metricsReceipt.digest, ledger: LEDGER }
  }

  // ── Cross-arm gate: mechanical rules computed HERE from verified payloads ──
  phase('Cross-arm gate')
  // Two comparisons, both required: the untrained control AND the trivial
  // TF-IDF baseline (control dev_ood = legacy "sealed" — the adaptive OOD
  // dev metric). A learned arm that cannot beat a trivial deterministic
  // baseline is never promotion-grade, whatever it does to the control.
  const control = metricsReceipt.payload.control
  if (!control) {
    return { verdict: 'REFUSED_NO_CONTROL', mode: MODE, receipt_sha256: metricsReceipt.digest, ledger: LEDGER }
  }
  const baseline = metricsReceipt.payload.baseline
  if (!baseline) {
    return { verdict: 'REFUSED_NO_BASELINE', mode: MODE, receipt_sha256: metricsReceipt.digest, ledger: LEDGER }
  }
  const controlDev = control.dev_ood
  const baselineDev = baseline.dev_ood
  const judged = armsPayload.map(record => {
    const armLanes = lanes.filter(l => l.arm === record.arm && l.in_template >= 0)
    const seedsRun = new Set(armLanes.map(l => l.seed)).size
    const vitalsOk = armLanes.length > 0 && armLanes.every(l => l.vitals_veto === 'ok')
    const retentionOk = armLanes.length > 0 && armLanes.every(l => /^3\/3$|^full$/i.test(l.retention))
    const devScores = armLanes.map(l => l.dev_ood)
    const minDev = devScores.length ? Math.min(...devScores) : -1
    const beatsControl = minDev > controlDev
    const beatsBaseline = minDev > baselineDev
    const multiSeedOk = seedsRun >= (MODE === 'mock' ? 1 : 3)
    return {
      arm: record.arm,
      results_name: record.results_name,
      dataset_hash: record.dataset_hash,
      seeds_run: seedsRun,
      vitals_ok: vitalsOk,
      retention_ok: retentionOk,
      min_dev_ood: minDev,
      beats_control: beatsControl,
      beats_baseline: beatsBaseline,
      multi_seed_ok: multiSeedOk,
      // Promotion-candidate rule: every mechanical gate green. In mock,
      // multi-seed is structurally impossible (1 historical seed) so no arm
      // can be promotion-grade — which is exactly the audited conclusion.
      promotion_candidate: vitalsOk && retentionOk && beatsControl && beatsBaseline && multiSeedOk && MODE === 'live',
    }
  })
  iterations.push({
    iter,
    arms: judged,
    control: { name: control.results_name, dev_ood: controlDev },
    baseline: { name: baseline.results_name, dev_ood: baselineDev },
  })

  if (STOP_AFTER === 'gate' && iter === MAX_ITER) break
  // Iterate only when live, authorized, and something is still improving
  // against BOTH the control and the trivial baseline.
  if (MODE === 'mock' || iter === MAX_ITER) break
  if (!judged.some(j => j.beats_control && j.beats_baseline)) break
}

if (STOP_AFTER === 'gate') {
  return { verdict: 'STOPPED_AFTER_GATE', mode: MODE, iterations, ledger: LEDGER }
}

// ── Phase 5: Evidence — harvest request, draft, adversarial claim check ─────
phase('Evidence')

// Typed next_harvest_request: derived by the validator from the SAME verified
// evidence (weak confusion cells, retention cells, exact digests). It is a
// REQUEST ONLY — it authorizes no generation and no training; acting on it
// requires a separate Codex-approved harvesting manifest.
const harvest = await gateReceipt(
  'harvest-request', 'Evidence', 'harvest_request',
  `harvest-request --packet ${PACKET} --campaign ${CAMPAIGN_ID} --source-dataset ${SOURCE_DATASET} --target-dataset ${TARGET_DATASET}`
)
if (harvest.payload.request_only !== true || harvest.payload.authorizes_generation !== false || harvest.payload.authorizes_training !== false) {
  throw new Error('gate failure: next_harvest_request must be request-only (authorizes_generation=false, authorizes_training=false)')
}

const last = iterations[iterations.length - 1]
const gateSummary = {
  preflight: { ok: preflight.payload.ok, violations: preflight.payload.violations, receipt_sha256: preflight.digest },
  corpus_veto: { ok: veto.payload.ok, violations: veto.payload.violations, receipt_sha256: veto.digest },
  arms: last.arms,
  control: last.control,
  baseline: last.baseline,
}

const draft = await call('report-draft', 'Evidence', `${COMMON}

Draft (in facts, as markdown) a concise campaign result summary for run ${RUN_STAMP}: mode; gate outcomes and per-arm table from this verified JSON: ${JSON.stringify(gateSummary)}. State plainly that mock mode trains nothing, that every gate decision came from a digest-verified evals.needle_gates receipt, and that promotion authority belongs to the Codex gate. Every sentence must restate the JSON above or cite a committed artifact by path — no claims about your own process or about anything not in the JSON.`, DRAFT_SCHEMA)

const check = await call('claim-verify', 'Evidence', `${COMMON}

Adversarially verify the draft below. For EVERY numeric or factual claim, name the committed artifact (file path) that backs it and verdict CONFIRMED, or REFUTED (artifact contradicts), or NO_ARTIFACT (nothing backs it). A claim sourced only from the workflow's own verified JSON is CONFIRMED with artifact "workflow-state" ONLY if it matches that JSON exactly. The verified JSON: ${JSON.stringify(gateSummary)}

DRAFT:
${draft.facts}`, CLAIMCHECK_SCHEMA, 'medium')

const unbacked = check.claims.filter(c => c.verdict !== 'CONFIRMED')
return {
  verdict: unbacked.length === 0 ? 'EVIDENCE_CLEAN' : 'EVIDENCE_GAPS',
  mode: MODE,
  run: RUN_STAMP,
  promotion: 'NONE — candidates + evidence only; promotion belongs to the Codex landing gate',
  iterations,
  gate_receipts: {
    preflight: preflight.digest,
    corpus_veto: veto.digest,
    arm_metrics: metricsReceipt ? metricsReceipt.digest : null,
    harvest_request: harvest.digest,
  },
  next_harvest_request: harvest.payload,
  report_draft: draft.facts,
  claim_check: { unbacked, total: check.claims.length },
  ledger: LEDGER,
}
