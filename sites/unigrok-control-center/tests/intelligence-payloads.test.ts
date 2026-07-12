import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { canonicalize, capsuleId, digestBody, validateBody } from "../app/lib/intelligence-capsule";
import {
  AGENTIC_DPO_PAIR_SCHEMA,
  CONFORMANCE_FILE,
  CONFORMANCE_SHA256,
  GNO_ENVELOPE_SCHEMA,
  OPTIBENCH_RESULT_SCHEMA,
  PROFILE_SCHEMA_FILES,
  PROFILE_SCHEMA_SHA256,
  PROJECTION_SCHEMA_FILES,
  PROJECTION_SCHEMA_SHA256,
  SEMANTIC_SPEC_FILE,
  SEMANTIC_SPEC_SHA256,
  SHARED_SECRET_PATTERNS,
  validateKnownPayloadProfile,
} from "../app/lib/intelligence-payloads";

type RecordValue = Record<string, unknown>;

const fixtureUrl = new URL("../../../tests/fixtures/intelligence_capsule/v1/golden-envelope.json", import.meta.url);
const conformanceUrl = new URL(`../../../docs/okf/${CONFORMANCE_FILE}`, import.meta.url);

const id = (digit: string) => `ucap1:sha256:${digit.repeat(64)}`;
const sha256 = (raw: Uint8Array | string) => createHash("sha256").update(raw).digest("hex");

function evidence(name: string, text: string, mediaType = "text/plain"): RecordValue {
  return { bytes: Buffer.byteLength(text), media_type: mediaType, name, sha256: sha256(text) };
}

function ref(item: RecordValue): RecordValue {
  return { evidence_name: item.name, sha256: item.sha256 };
}

async function baseBody(): Promise<RecordValue> {
  const envelope = JSON.parse(await readFile(fixtureUrl, "utf8"));
  return envelope.body;
}

async function body(
  kind: string,
  schema: string,
  data: RecordValue,
  evidenceItems: RecordValue[],
  parents: string[],
  metrics?: RecordValue[],
): Promise<RecordValue> {
  const value = await baseBody();
  value.kind = kind;
  value.parents = [...parents].sort();
  value.evidence = [...evidenceItems].sort((left, right) =>
    `${String(left.name)}\0${String(left.sha256)}`.localeCompare(`${String(right.name)}\0${String(right.sha256)}`));
  value.payload = { data, schema };
  if (metrics) value.metrics = metrics;
  else delete value.metrics;
  return value;
}

async function gnoBody(): Promise<RecordValue> {
  const taskSpec = evidence("task-spec", "optimize foo without ambient reads");
  const routing = {
    algorithm: "discounted_ucb_v1",
    arm: "hot_loop",
    arm_pull: 7,
    parameters: [{ name: "decay", value: "0.95" }, { name: "exploration", value: "1" }],
    reason: "discounted_ucb",
    seed: 42,
    step: 25,
  };
  const execution = (await baseBody()).execution;
  const declared = [{ role: "task_spec", ...ref(taskSpec) }];
  const inputManifestText = Buffer.from(canonicalize({
    declared_artifacts: declared,
    execution,
    routing,
  })).toString("utf8");
  const inputManifest = evidence("input-manifest", inputManifestText, "application/json");
  return body("task", GNO_ENVELOPE_SCHEMA, {
    artifacts: [
      { role: "input_manifest", ...ref(inputManifest) },
      ...declared,
    ],
    generation: 1,
    mutator: { origin: "agent", profile: "hot_loop" },
    node_id: "node-01",
    phase: "dispatch",
    routing,
    slot: 2,
  }, [inputManifest, taskSpec], [id("1")]);
}

async function optibenchBody(): Promise<RecordValue> {
  const items = Object.fromEntries(Object.entries({
    "counter-output": "1,234 instructions",
    environment: '{"runner":"linux-x86_64"}',
    harness: '{"tokenizer":"pinned-v1"}',
    population: '{"closed":true}',
    tests: '{"passed":true}',
  }).map(([name, text]) => [name, evidence(name, text)]));
  const candidate = id("2");
  return body("benchmark", OPTIBENCH_RESULT_SCHEMA, {
    aggregation: "median",
    baseline_relation: "dominates_baseline",
    candidate_capsule: candidate,
    cohort: {
      counter_profile: "perf_stat_instructions_v1",
      environment: ref(items.environment),
      harness: ref(items.harness),
      tool: "perf",
      tool_version: "6.8.0",
    },
    counter_output: ref(items["counter-output"]),
    gate_receipts: [ref(items.tests)],
    nsga2: { algorithm: "nsga2_exact_v1", crowding: { kind: "boundary" }, pareto_rank: 0 },
    objective_metrics: ["cpu_instructions_retired", "diff_tokens", "peak_memory_bytes"],
    population_closed: true,
    population_manifest: ref(items.population),
    repetitions: 5,
  }, Object.values(items), [candidate], [
    { name: "cpu_instructions_retired", unit: "instruction", value: "1234" },
    { name: "diff_tokens", unit: "token", value: "18" },
    { name: "peak_memory_bytes", unit: "byte", value: "29300" },
  ]);
}

async function dpoBody(): Promise<RecordValue> {
  const items = Object.fromEntries(Object.entries({
    "chosen-output": "optimized implementation",
    "cohort-proof": '{"benchmarks":[]}',
    dominance: '{"chosen":[10,20,3],"rejected":[12,25,4]}',
    population: '{"closed":true,"cohort":"abc"}',
    prompt: "Optimize the focus function.",
    "rejected-output": "slower implementation",
  }).map(([name, text]) => [name, evidence(name, text)]));
  const task = id("1");
  const chosenCandidate = id("2");
  const chosenBenchmark = id("3");
  const rejectedCandidate = id("4");
  const rejectedBenchmark = id("5");
  return body("evaluation", AGENTIC_DPO_PAIR_SCHEMA, {
    chosen: {
      artifact: ref(items["chosen-output"]),
      benchmark_capsule: chosenBenchmark,
      candidate_capsule: chosenCandidate,
    },
    cohort_proof_manifest: ref(items["cohort-proof"]),
    export_profile: "preference_jsonl_v1",
    population_manifest: ref(items.population),
    preference: {
      basis: "pareto_dominance",
      dominance_receipt: ref(items.dominance),
      policy: "nsga2_direct_dominance_v1",
    },
    prompt: ref(items.prompt),
    rejected: {
      artifact: ref(items["rejected-output"]),
      benchmark_capsule: rejectedBenchmark,
      candidate_capsule: rejectedCandidate,
    },
    task_capsule: task,
  }, Object.values(items), [task, chosenCandidate, chosenBenchmark, rejectedCandidate, rejectedBenchmark]);
}

function validateKnownBody(value: RecordValue): void {
  validateBody(value);
  assert.equal(validateKnownPayloadProfile(value), true);
}

test("payload schema files are independently digest pinned", async () => {
  for (const [schema, fileName] of Object.entries(PROFILE_SCHEMA_FILES)) {
    const raw = await readFile(new URL(`../../../docs/okf/${fileName}`, import.meta.url));
    assert.equal(sha256(raw), PROFILE_SCHEMA_SHA256[schema as keyof typeof PROFILE_SCHEMA_SHA256]);
  }
  for (const [schema, fileName] of Object.entries(PROJECTION_SCHEMA_FILES)) {
    const raw = await readFile(new URL(`../../../docs/okf/${fileName}`, import.meta.url));
    assert.equal(sha256(raw), PROJECTION_SCHEMA_SHA256[schema as keyof typeof PROJECTION_SCHEMA_SHA256]);
  }
  const semanticSpec = await readFile(new URL(`../../../docs/okf/${SEMANTIC_SPEC_FILE}`, import.meta.url));
  assert.equal(sha256(semanticSpec), SEMANTIC_SPEC_SHA256);
  assert.equal(sha256(await readFile(conformanceUrl)), CONFORMANCE_SHA256);
  const semanticDocument = JSON.parse(semanticSpec.toString("utf8"));
  assert.deepEqual(
    SHARED_SECRET_PATTERNS.map((pattern) => ({
      expression: pattern.source,
      ignore_case: pattern.ignoreCase,
    })),
    semanticDocument.secret_rejection.patterns,
  );
});

test("TypeScript and Python accept identical known-profile bodies and identities", async () => {
  const vectors = JSON.parse(await readFile(conformanceUrl, "utf8"));
  for (const knownBody of [await gnoBody(), await optibenchBody(), await dpoBody()]) {
    validateKnownBody(knownBody);
    const schema = (knownBody.payload as RecordValue).schema as string;
    const expected = vectors.accepted_body_sha256[schema];
    assert.equal(await digestBody(knownBody), expected);
    assert.equal(await capsuleId(knownBody), `ucap1:sha256:${expected}`);
  }
});

test("known-profile validation rejects substituted evidence and mixed cohorts", async () => {
  const vectors = JSON.parse(await readFile(conformanceUrl, "utf8"));
  const oversizedDigits = vectors.reject_vectors.find(
    (item: RecordValue) => item.mutation === "crowding_numerator_digits",
  ).value as number;
  const gno = await gnoBody();
  ((((gno.payload as RecordValue).data as RecordValue).artifacts as RecordValue[])[0]).sha256 = "f".repeat(64);
  assert.throws(() => validateKnownBody(gno), /does not match/);

  const optibench = await optibenchBody();
  (((optibench.payload as RecordValue).data as RecordValue).cohort as RecordValue).counter_profile = "callgrind_ir_v1";
  assert.throws(() => validateKnownBody(optibench), /profile and tool disagree/);

  const oversized = await optibenchBody();
  ((((oversized.payload as RecordValue).data as RecordValue).nsga2 as RecordValue).crowding as RecordValue) = {
    denominator: "1",
    kind: "finite",
    numerator: "9".repeat(oversizedDigits),
  };
  assert.throws(() => validateKnownBody(oversized), /numerator/);

  const secretVector = vectors.reject_vectors.find(
    (item: RecordValue) => item.mutation === "secret_identifier",
  );
  assert.ok(secretVector);
  const secretValue = (secretVector.value_parts as string[]).join("");
  const secretName = await gnoBody();
  (secretName.evidence as RecordValue[]).push(
    evidence(secretValue, "unused non-secret evidence"),
  );
  (secretName.evidence as RecordValue[]).sort((left, right) =>
    `${String(left.name)}\0${String(left.sha256)}`.localeCompare(`${String(right.name)}\0${String(right.sha256)}`));
  assert.throws(() => validateKnownBody(secretName), /secret-like/);

  const secretActor = await gnoBody();
  (secretActor.actor as RecordValue).agent_id = secretValue;
  validateBody(secretActor);
  assert.throws(() => validateKnownPayloadProfile(secretActor), /secret-like/);

  const enumVector = vectors.reject_vectors.find(
    (item: RecordValue) => item.mutation === "enum_type_confusion",
  );
  assert.ok(enumVector);
  const confused = await gnoBody();
  ((((confused.payload as RecordValue).data as RecordValue).mutator as RecordValue).origin) = enumVector.value;
  validateBody(confused);
  assert.throws(() => validateKnownPayloadProfile(confused), /origin is unsupported/);
});

test("known-profile validation rejects arbitrary preference parents", async () => {
  const dpo = await dpoBody();
  (dpo.parents as string[]).pop();
  assert.throws(() => validateKnownBody(dpo), /parents must be/);
});
