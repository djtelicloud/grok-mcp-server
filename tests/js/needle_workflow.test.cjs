'use strict'
// Executable test for .claude/workflows/needle-training-campaign.js.
//
// Runs the workflow body with a mock agent runtime whose "agents" execute the
// EXACT validator commands the workflow constructs (via spawnSync with an
// argument array — no shell), then transcribe the emitted receipts, exactly
// as the real agents are instructed to. Covers:
//   • command construction: only safe single-token values reach commands;
//     hostile run_stamp / campaign / dataset / packet values throw before
//     any agent call
//   • receipt consumption: digests recomputed, tampered receipts refused
//   • cross-arm gating: control AND trivial tf-idf baseline comparisons
//   • live-mode refusals (no manifest; mock fixture as live packet)
//
// Invoked by tests/test_needle_workflow_js.py (skipped when node or uv is
// unavailable). Runs nothing live: mock mode replays the committed fixture.

const assert = require('node:assert')
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
const WORKFLOW_PATH = path.join(REPO_ROOT, '.claude', 'workflows', 'needle-training-campaign.js')
const FIXTURE_PACKET = 'evals/needle_gates/fixtures/mock_packet'

const SAFE_TOKEN = /^[A-Za-z0-9._/-]+$|^--[a-z-]+$/

function loadWorkflow() {
  const source = fs.readFileSync(WORKFLOW_PATH, 'utf8')
  const body = source.replace('export const meta =', 'const meta =')
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  return new AsyncFunction('args', 'agent', 'phase', body)
}

// Mock agent runtime: executes the exact CLI command found in the prompt.
function makeAgentRuntime(options = {}) {
  const calls = []
  const commandLines = []
  async function agent(prompt, opts) {
    calls.push({ label: opts && opts.label })
    const match = /^\s*uv run python -m evals\.needle_gates (.+)$/m.exec(prompt)
    if (match) {
      const tokens = match[1].trim().split(/\s+/)
      commandLines.push(tokens.join(' '))
      for (const token of tokens) {
        assert.ok(
          SAFE_TOKEN.test(token),
          `workflow constructed a command token outside the safe charset: ${JSON.stringify(token)}`
        )
      }
      const result = spawnSync(
        'uv',
        ['run', 'python', '-m', 'evals.needle_gates', ...tokens],
        { cwd: REPO_ROOT, encoding: 'utf8', shell: false, timeout: 120000 }
      )
      assert.strictEqual(result.status, 0, `validator CLI failed: ${result.stderr}`)
      let receipt = result.stdout
      if (options.tamperLabel && opts.label === options.tamperLabel) {
        const parsed = JSON.parse(receipt)
        const flipped = (parsed.payload_b64[0] === 'A' ? 'B' : 'A') + parsed.payload_b64.slice(1)
        receipt = JSON.stringify({ ...parsed, payload_b64: flipped })
      }
      return { receipt, facts: `ran ${tokens[0]}` }
    }
    if (opts && opts.label === 'report-draft') {
      return { facts: 'Mock-mode campaign summary derived from workflow-state.' }
    }
    if (opts && opts.label === 'claim-verify') {
      return {
        claims: [{ claim: 'summary matches verified JSON', artifact: 'workflow-state', verdict: 'CONFIRMED' }],
        facts: 'all claims confirmed against workflow-state',
      }
    }
    throw new Error(`unexpected agent prompt for label ${opts && opts.label}`)
  }
  return { agent, calls, commandLines }
}

const phases = []
function phase(name) {
  phases.push(name)
}

async function expectThrow(fn, pattern, agentCalls, label) {
  let threw = null
  try {
    await fn()
  } catch (err) {
    threw = err
  }
  assert.ok(threw, `${label}: expected workflow to throw`)
  assert.ok(
    pattern.test(String(threw.message || threw)),
    `${label}: unexpected error message: ${threw.message}`
  )
  if (agentCalls) {
    assert.strictEqual(agentCalls.length, 0, `${label}: agents were called before validation refused the input`)
  }
  console.log(`ok - ${label}`)
}

async function main() {
  const workflow = loadWorkflow()

  // 1. Happy path: full mock run on the committed fixture packet.
  {
    const runtime = makeAgentRuntime()
    const result = await workflow(
      { mode: 'mock', run_stamp: 'js-test-run', campaign_id: 'needle-js', source_dataset_id: 'D0001', target_dataset_id: 'D0002' },
      runtime.agent,
      phase
    )
    assert.strictEqual(result.verdict, 'EVIDENCE_CLEAN')
    assert.strictEqual(result.mode, 'mock')
    // Receipt consumption: every gate decision carries a verified digest.
    for (const key of ['preflight', 'corpus_veto', 'arm_metrics', 'harvest_request']) {
      assert.match(result.gate_receipts[key], /^[0-9a-f]{64}$/, `missing verified digest for ${key}`)
    }
    // Command construction: the workflow embedded the fixture packet and the
    // validated identifiers, nothing else.
    assert.ok(runtime.commandLines.some(c => c.includes(`--packet ${FIXTURE_PACKET}`)))
    assert.ok(runtime.commandLines.some(c => c.includes('--campaign needle-js')))
    // Cross-arm gating: control AND trivial baseline comparisons, from the
    // fixture numbers (B beats control only; E beats both).
    const iteration = result.iterations[result.iterations.length - 1]
    assert.strictEqual(iteration.control.name, 'A_control')
    assert.strictEqual(iteration.baseline.name, 'tfidf_baseline')
    assert.ok(iteration.baseline.dev_ood > iteration.control.dev_ood)
    const byArm = Object.fromEntries(iteration.arms.map(a => [a.arm, a]))
    assert.strictEqual(byArm.arm_B.beats_control, true)
    assert.strictEqual(byArm.arm_B.beats_baseline, false, 'trivial baseline must gate arms that only beat the control')
    assert.strictEqual(byArm.arm_E.beats_control, true)
    assert.strictEqual(byArm.arm_E.beats_baseline, true)
    assert.ok(iteration.arms.every(a => a.promotion_candidate === false), 'mock mode can never mark promotion candidates')
    assert.ok(iteration.arms.every(a => a.results_name && a.dataset_hash), 'judged arms must carry their exact identity binding')
    // The typed harvest request is request-only and carries originating roots.
    assert.strictEqual(result.next_harvest_request.request_only, true)
    assert.strictEqual(result.next_harvest_request.authorizes_generation, false)
    assert.ok(result.next_harvest_request.weak_confusion_cells.every(c => Array.isArray(c.root_ids)))
    console.log('ok - mock run on committed fixture: gating + receipts + harvest request')
  }

  // 2-5. Hostile embedded values throw before any agent call.
  for (const [label, args] of [
    ['hostile run_stamp rejected', { mode: 'mock', run_stamp: 'x;rm -rf /tmp/x' }],
    ['hostile campaign_id rejected', { mode: 'mock', run_stamp: 'ok-run', campaign_id: '$(whoami)' }],
    ['hostile dataset id rejected', { mode: 'mock', run_stamp: 'ok-run', source_dataset_id: 'D0001|tee /tmp/x' }],
    ['hostile packet path rejected', { mode: 'mock', run_stamp: 'ok-run', packet: '../../needle_lab' }],
    ['whitespace packet rejected', { mode: 'mock', run_stamp: 'ok-run', packet: 'evals/needle_lab; touch pwned' }],
  ]) {
    const runtime = makeAgentRuntime()
    await expectThrow(
      () => workflow(args, runtime.agent, phase),
      /rejected/,
      runtime.calls,
      label
    )
  }

  // 6. Tampered receipt is refused (digest recomputed by the workflow).
  {
    const runtime = makeAgentRuntime({ tamperLabel: 'corpus-veto' })
    await expectThrow(
      () => workflow({ mode: 'mock', run_stamp: 'tamper-run' }, runtime.agent, phase),
      /digest|base64|mismatch/i,
      null,
      'tampered receipt refused'
    )
  }

  // 7. Live mode refusals: no manifest; mock fixture as live packet.
  {
    const runtime = makeAgentRuntime()
    await expectThrow(
      () => workflow({ mode: 'live', run_stamp: 'live-run' }, runtime.agent, phase),
      /live mode refused/,
      runtime.calls,
      'live mode without manifest refused'
    )
  }
  {
    const runtime = makeAgentRuntime()
    await expectThrow(
      () => workflow(
        {
          mode: 'live',
          run_stamp: 'live-run',
          manifest: {
            training_enabled: true,
            packet: FIXTURE_PACKET,
            receipt_digest_pins: { preflight: 'x', corpus_veto: 'x', arm_records: 'x' },
          },
        },
        runtime.agent,
        phase
      ),
      /live mode refused/,
      runtime.calls,
      'mock fixture as live packet refused'
    )
  }

  console.log('all needle workflow JS tests passed')
}

main().catch(err => {
  console.error(err && err.stack ? err.stack : String(err))
  process.exit(1)
})
