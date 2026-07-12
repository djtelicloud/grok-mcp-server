export const GNO_ENVELOPE_SCHEMA = "org.grokmcp.gno_envelope.v1";
export const OPTIBENCH_RESULT_SCHEMA = "org.grokmcp.optibench_result.v1";
export const AGENTIC_DPO_PAIR_SCHEMA = "org.grokmcp.agentic_dpo_pair.v1";
export const NEEDLE_CONTEXT_PROFILE = "org.grokmcp.needle_tools_context.v1";

export const PROFILE_SCHEMA_FILES = Object.freeze({
  [GNO_ENVELOPE_SCHEMA]: "gno-envelope-v1.schema.json",
  [OPTIBENCH_RESULT_SCHEMA]: "optibench-result-v1.schema.json",
  [AGENTIC_DPO_PAIR_SCHEMA]: "agentic-dpo-pair-v1.schema.json",
} as const);

export const PROFILE_SCHEMA_SHA256 = Object.freeze({
  [GNO_ENVELOPE_SCHEMA]: "4c7fb150b3f82738ae43d52669c8c663283807d42add1f4532f01527a4d70665",
  [OPTIBENCH_RESULT_SCHEMA]: "dfc216d1855eb36e54829c3aca00434f0dc9845a6efc205c2c49016531accf81",
  [AGENTIC_DPO_PAIR_SCHEMA]: "7db601ccc11aaa94409383f88c7305a46b63a705176897aaa313f835b24bed84",
} as const);

export const PROJECTION_SCHEMA_FILES = Object.freeze({
  [NEEDLE_CONTEXT_PROFILE]: "needle-tools-context-v1.schema.json",
} as const);

export const PROJECTION_SCHEMA_SHA256 = Object.freeze({
  [NEEDLE_CONTEXT_PROFILE]: "ac92a88b87e35254a7eef4a151d8743418ef102402022b228609743cbcbf7496",
} as const);

export const SEMANTIC_SPEC_FILE = "intelligence-payload-semantics-v1.json";
export const SEMANTIC_SPEC_SHA256 = "7464c2343c3edaadc21a14a880e689ef8e4b4ac0fa3fc07b2b6f37b08733545a";
export const CONFORMANCE_FILE = "intelligence-payload-conformance-v1.json";
export const CONFORMANCE_SHA256 = "6a0df82c82cd3bfbadc6ff1febf1e43b2d2a6446acd1b977ae7d3262de8d98f4";

const KNOWN = new Set(Object.keys(PROFILE_SCHEMA_FILES));
const CAPSULE_ID_RE = /^ucap1:sha256:[a-f0-9]{64}$/;
const DECIMAL_RE = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const NONNEGATIVE_INTEGER_RE = /^(?:0|[1-9][0-9]*)$/;
const POSITIVE_INTEGER_RE = /^[1-9][0-9]*$/;
const MAX_RATIONAL_DIGITS = 128;
const MAX_OBJECTIVE_DIGITS = 32;
export const SHARED_SECRET_PATTERNS = Object.freeze([
  /github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9]{10,}/,
  /glpat-[A-Za-z0-9_-]{10,}/,
  /\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}/,
  /\bxai-[A-Za-z0-9_-]{12,}/i,
  /\bAIza[A-Za-z0-9_-]{25,}/,
  /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/,
  /\bnpm_[A-Za-z0-9]{20,}/,
  /\bpypi-[A-Za-z0-9_-]{20,}/,
  /\bxox[baprs]-[A-Za-z0-9-]{12,}/,
  /\bsk_live_[A-Za-z0-9]{16,}/,
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/,
  /\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/,
  /\bBearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+/=-]{8,}/i,
  /Authorization\s*:\s*Bearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+/-]{8,}/i,
  /Authorization\s*:\s*Basic\s+(?!<|\$\{|\[)[A-Za-z0-9+/=]{8,}/i,
  /(?:^|[^A-Za-z0-9_])["']?[A-Z0-9_-]*(?:API[_-]?KEY|SECRET[_-]?ACCESS[_-]?KEY|SESSION[_-]?TOKEN|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)["']?\s*[:=]\s*(?!["']?(?:<|\$\{|\[))(?:"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|[A-Za-z0-9._~+/=@!#$%^&*()-]{8,})/i,
] as const);

const PERF_OBJECTIVES = [
  ["cpu_instructions_retired", "instruction"],
  ["diff_tokens", "token"],
  ["peak_memory_bytes", "byte"],
] as const;
const CALLGRIND_OBJECTIVES = [
  ["callgrind_ir", "instruction_reference"],
  ["diff_tokens", "token"],
  ["peak_memory_bytes", "byte"],
] as const;

type Fail = (message: string) => never;
type EvidenceIndex = Map<string, Record<string, unknown>>;

/** Validate a known profile after generic Capsule v1 validation. */
export function validateKnownPayloadProfile(
  body: Record<string, unknown>,
  fail: Fail = (message) => { throw new Error(message); },
): boolean {
  const payload = body.payload;
  if (!isRecord(payload) || typeof payload.schema !== "string" || !KNOWN.has(payload.schema)) return false;
  rejectSecretStrings(body, "$.body", fail);
  const evidence = evidenceIndex(body.evidence, fail);
  if (payload.schema === GNO_ENVELOPE_SCHEMA) validateGno(body, payload.data, evidence, fail);
  else if (payload.schema === OPTIBENCH_RESULT_SCHEMA) validateOptibench(body, payload.data, evidence, fail);
  else validateDpo(body, payload.data, evidence, fail);
  return true;
}

function validateGno(body: Record<string, unknown>, raw: unknown, evidence: EvidenceIndex, fail: Fail): void {
  const path = "$.body.payload.data";
  const value = record(raw, path, fail);
  const phase = value.phase;
  const common = ["artifacts", "generation", "node_id", "phase", "slot"];
  if (phase === "dispatch") {
    exactKeys(value, [...common, "mutator", "routing"], path, fail);
    if (body.kind !== "task") fail("gno dispatch requires body.kind task");
    if (!isRecord(body.execution)) fail("gno dispatch requires explicit execution metadata");
  } else if (phase === "result") {
    exactKeys(value, [...common, "dispatch_capsule", "outcome"], path, fail);
    const expectedKind = value.outcome === "candidate" ? "candidate" : value.outcome === "failure" ? "failure" : undefined;
    if (!expectedKind || body.kind !== expectedKind) fail("gno result outcome must match candidate or failure body.kind");
    const dispatch = capsuleId(value.dispatch_capsule, `${path}.dispatch_capsule`, fail);
    if (!Array.isArray(body.parents) || body.parents.length !== 1 || body.parents[0] !== dispatch) {
      fail("gno result must have its dispatch capsule as its sole parent");
    }
    if (Object.hasOwn(body, "execution")) {
      fail("gno result inherits execution from its dispatch and must not duplicate it");
    }
  } else fail("gno envelope phase must be dispatch or result");

  identifier(value.node_id, `${path}.node_id`, fail);
  positiveSafeInt(value.generation, `${path}.generation`, fail);
  positiveSafeInt(value.slot, `${path}.slot`, fail);
  const allowed = phase === "dispatch"
    ? new Set(["context", "input_diff", "input_manifest", "policy", "system_prompt", "task_spec", "tool_manifest"])
    : new Set(["failure_receipt", "output_diff", "test_receipt", "trace"]);
  const artifacts = artifactRefs(value.artifacts, evidence, `${path}.artifacts`, allowed, fail);
  const roleCount = (role: string) => artifacts.filter((item) => item.role === role).length;
  if (phase === "dispatch") {
    if (roleCount("task_spec") !== 1 || roleCount("input_manifest") !== 1) {
      fail("gno dispatch requires exactly one input_manifest and task_spec");
    }
    for (const singleton of ["input_diff", "policy", "system_prompt", "tool_manifest"]) {
      if (roleCount(singleton) > 1) fail(`gno dispatch permits at most one ${singleton} artifact`);
    }
  }
  if (phase === "result") {
    const required = value.outcome === "candidate" ? "output_diff" : "failure_receipt";
    const forbidden = value.outcome === "candidate" ? "failure_receipt" : "output_diff";
    if (roleCount(required) !== 1) fail(`gno ${String(value.outcome)} result requires exactly one ${required}`);
    if (roleCount(forbidden) !== 0) fail(`gno ${String(value.outcome)} result forbids ${forbidden}`);
    for (const singleton of ["test_receipt", "trace"]) {
      if (roleCount(singleton) > 1) fail(`gno result permits at most one ${singleton} artifact`);
    }
  }
  if (phase === "dispatch") {
    const mutator = record(value.mutator, `${path}.mutator`, fail);
    exactKeys(mutator, ["origin", "profile"], `${path}.mutator`, fail);
    if (typeof mutator.origin !== "string" || !new Set(["agent", "ast", "baseline"]).has(mutator.origin)) fail("gno mutator.origin is unsupported");
    identifier(mutator.profile, `${path}.mutator.profile`, fail);
    routing(value.routing, fail);
  }
}

function routing(raw: unknown, fail: Fail): void {
  const path = "$.body.payload.data.routing";
  const value = record(raw, path, fail);
  exactKeys(value, ["algorithm", "arm", "arm_pull", "parameters", "reason", "seed", "step"], path, fail);
  const algorithms = new Set(["deterministic_v1", "discounted_ucb_v1", "round_robin_v1", "ucb1_v1"]);
  if (typeof value.algorithm !== "string" || !algorithms.has(value.algorithm)) fail(`${path}.algorithm is unsupported`);
  identifier(value.arm, `${path}.arm`, fail);
  identifier(value.reason, `${path}.reason`, fail);
  nonnegativeSafeInt(value.seed, `${path}.seed`, fail);
  positiveSafeInt(value.step, `${path}.step`, fail);
  positiveSafeInt(value.arm_pull, `${path}.arm_pull`, fail);
  const parameters = array(value.parameters, `${path}.parameters`, 32, fail);
  const order: string[] = [];
  parameters.forEach((rawParameter, index) => {
    const itemPath = `${path}.parameters[${index}]`;
    const item = record(rawParameter, itemPath, fail);
    exactKeys(item, ["name", "value"], itemPath, fail);
    order.push(identifier(item.name, `${itemPath}.name`, fail));
    if (typeof item.value !== "string" || !DECIMAL_RE.test(item.value)) fail(`${itemPath}.value must be a decimal string`);
  });
  sortedUnique(order, `${path}.parameters`, fail);
}

function validateOptibench(body: Record<string, unknown>, raw: unknown, evidence: EvidenceIndex, fail: Fail): void {
  const path = "$.body.payload.data";
  const value = record(raw, path, fail);
  exactKeys(value, [
    "aggregation", "baseline_relation", "candidate_capsule", "cohort", "counter_output", "gate_receipts",
    "nsga2", "objective_metrics", "population_closed", "population_manifest", "repetitions",
  ], path, fail);
  if (body.kind !== "benchmark") fail("optibench result requires body.kind benchmark");
  if (value.population_closed !== true) fail("optibench population must be closed before ranking");
  const candidate = capsuleId(value.candidate_capsule, `${path}.candidate_capsule`, fail);
  if (!Array.isArray(body.parents) || !body.parents.includes(candidate) || body.parents.length === 0) {
    fail("optibench candidate must be a population parent");
  }
  matchEvidenceRef(value.population_manifest, evidence, `${path}.population_manifest`, fail);
  matchEvidenceRef(value.counter_output, evidence, `${path}.counter_output`, fail);

  const cohort = record(value.cohort, `${path}.cohort`, fail);
  exactKeys(cohort, ["counter_profile", "environment", "harness", "tool", "tool_version"], `${path}.cohort`, fail);
  let objectives: ReadonlyArray<readonly [string, string]>;
  let tool: string;
  if (cohort.counter_profile === "perf_stat_instructions_v1") {
    objectives = PERF_OBJECTIVES;
    tool = "perf";
  } else if (cohort.counter_profile === "callgrind_ir_v1") {
    objectives = CALLGRIND_OBJECTIVES;
    tool = "valgrind";
  } else fail("optibench counter_profile is unsupported");
  if (cohort.tool !== tool) fail("optibench counter_profile and tool disagree");
  identifier(cohort.tool_version, `${path}.cohort.tool_version`, fail);
  for (const field of ["environment", "harness"]) {
    matchEvidenceRef(cohort[field], evidence, `${path}.cohort.${field}`, fail);
  }

  const receipts = array(value.gate_receipts, `${path}.gate_receipts`, 32, fail);
  if (receipts.length === 0) fail("optibench requires at least one gate receipt");
  const receiptOrder = receipts.map((receipt, index) => {
    const descriptor = matchEvidenceRef(receipt, evidence, `${path}.gate_receipts[${index}]`, fail);
    return `${String(descriptor.name)}\0${String(descriptor.sha256)}`;
  });
  sortedUnique(receiptOrder, `${path}.gate_receipts`, fail);

  const expectedNames = objectives!.map(([name]) => name);
  if (!sameStringArray(value.objective_metrics, expectedNames)) fail("optibench objective_metrics must exactly match its counter cohort");
  if (!Array.isArray(body.metrics)) fail("optibench requires body.metrics");
  const actual = body.metrics.map((rawMetric) => {
    const metric = record(rawMetric, "$.body.metrics[]", fail);
    if (typeof metric.value !== "string" || metric.value.length > MAX_OBJECTIVE_DIGITS || !NONNEGATIVE_INTEGER_RE.test(metric.value)) {
      fail("optibench objective values must be nonnegative integer strings");
    }
    return [String(metric.name), String(metric.unit)] as const;
  });
  if (JSON.stringify(actual) !== JSON.stringify(objectives!)) fail("optibench body.metrics must exactly match its counter cohort");
  if (value.aggregation !== "median") fail("optibench aggregation must be median");
  const repetitions = positiveSafeInt(value.repetitions, `${path}.repetitions`, fail);
  if (repetitions % 2 === 0) fail("optibench repetitions must be odd for an integer median");
  const relations = new Set(["dominated_by_baseline", "dominates_baseline", "equal_baseline", "incomparable_to_baseline"]);
  if (typeof value.baseline_relation !== "string" || !relations.has(value.baseline_relation)) fail("optibench baseline_relation is unsupported");

  const nsga2 = record(value.nsga2, `${path}.nsga2`, fail);
  exactKeys(nsga2, ["algorithm", "crowding", "pareto_rank"], `${path}.nsga2`, fail);
  if (nsga2.algorithm !== "nsga2_exact_v1") fail("optibench nsga2 algorithm must be nsga2_exact_v1");
  nonnegativeSafeInt(nsga2.pareto_rank, `${path}.nsga2.pareto_rank`, fail);
  crowding(nsga2.crowding, `${path}.nsga2.crowding`, fail);
}

function crowding(raw: unknown, path: string, fail: Fail): void {
  const value = record(raw, path, fail);
  if (value.kind === "boundary") {
    exactKeys(value, ["kind"], path, fail);
    return;
  }
  if (value.kind !== "finite") fail(`${path}.kind must be boundary or finite`);
  exactKeys(value, ["denominator", "kind", "numerator"], path, fail);
  if (typeof value.numerator !== "string" || value.numerator.length > MAX_RATIONAL_DIGITS || !NONNEGATIVE_INTEGER_RE.test(value.numerator)) fail(`${path}.numerator must be a nonnegative integer string`);
  if (typeof value.denominator !== "string" || value.denominator.length > MAX_RATIONAL_DIGITS || !POSITIVE_INTEGER_RE.test(value.denominator)) fail(`${path}.denominator must be a positive integer string`);
  if (gcd(BigInt(value.numerator), BigInt(value.denominator)) !== BigInt(1)) fail(`${path} finite rational must be reduced`);
}

function validateDpo(body: Record<string, unknown>, raw: unknown, evidence: EvidenceIndex, fail: Fail): void {
  const path = "$.body.payload.data";
  const value = record(raw, path, fail);
  exactKeys(value, ["chosen", "cohort_proof_manifest", "export_profile", "population_manifest", "preference", "prompt", "rejected", "task_capsule"], path, fail);
  if (body.kind !== "evaluation") fail("agentic DPO pair requires body.kind evaluation");
  if (Array.isArray(body.metrics) && body.metrics.length > 0) fail("agentic DPO pair does not duplicate benchmark metrics");
  const task = capsuleId(value.task_capsule, `${path}.task_capsule`, fail);
  matchEvidenceRef(value.cohort_proof_manifest, evidence, `${path}.cohort_proof_manifest`, fail);
  matchEvidenceRef(value.population_manifest, evidence, `${path}.population_manifest`, fail);
  matchEvidenceRef(value.prompt, evidence, `${path}.prompt`, fail);
  const expected = new Set([task]);
  const sides: Record<string, Record<string, unknown>> = {};
  for (const side of ["chosen", "rejected"]) {
    const itemPath = `${path}.${side}`;
    const item = record(value[side], itemPath, fail);
    exactKeys(item, ["artifact", "benchmark_capsule", "candidate_capsule"], itemPath, fail);
    expected.add(capsuleId(item.candidate_capsule, `${itemPath}.candidate_capsule`, fail));
    expected.add(capsuleId(item.benchmark_capsule, `${itemPath}.benchmark_capsule`, fail));
    matchEvidenceRef(item.artifact, evidence, `${itemPath}.artifact`, fail);
    sides[side] = item;
  }
  const expectedParents = [...expected].sort();
  if (expected.size !== 5 || !sameStringArray(body.parents, expectedParents)) {
    fail("agentic DPO parents must be the task and distinct chosen/rejected candidate and benchmark capsules");
  }
  const chosenArtifact = record(sides.chosen.artifact, `${path}.chosen.artifact`, fail);
  const rejectedArtifact = record(sides.rejected.artifact, `${path}.rejected.artifact`, fail);
  if (chosenArtifact.sha256 === rejectedArtifact.sha256) fail("agentic DPO chosen and rejected artifacts must differ");

  const preference = record(value.preference, `${path}.preference`, fail);
  exactKeys(preference, ["basis", "dominance_receipt", "policy"], `${path}.preference`, fail);
  if (preference.basis !== "pareto_dominance") fail("agentic DPO v1 only supports direct Pareto dominance");
  if (preference.policy !== "nsga2_direct_dominance_v1") fail("agentic DPO preference policy is unsupported");
  matchEvidenceRef(preference.dominance_receipt, evidence, `${path}.preference.dominance_receipt`, fail);
  if (value.export_profile !== "preference_jsonl_v1") fail("agentic DPO export_profile is unsupported");
}

function evidenceIndex(raw: unknown, fail: Fail): EvidenceIndex {
  const values = array(raw, "$.body.evidence", 256, fail);
  const result: EvidenceIndex = new Map();
  values.forEach((rawItem, index) => {
    const item = record(rawItem, `$.body.evidence[${index}]`, fail);
    const name = identifier(item.name, `$.body.evidence[${index}].name`, fail);
    if (result.has(name)) fail("known payload profiles require unique evidence names");
    result.set(name, item);
  });
  return result;
}

function artifactRefs(raw: unknown, evidence: EvidenceIndex, path: string, allowed: Set<string>, fail: Fail): Record<string, unknown>[] {
  const values = array(raw, path, 64, fail);
  if (values.length === 0) fail(`${path} must not be empty`);
  const order: string[] = [];
  const result = values.map((rawItem, index) => {
    const itemPath = `${path}[${index}]`;
    const item = record(rawItem, itemPath, fail);
    exactKeys(item, ["evidence_name", "role", "sha256"], itemPath, fail);
    if (typeof item.role !== "string" || !allowed.has(item.role)) fail(`${itemPath}.role is unsupported`);
    const descriptor = matchEvidenceRef(item, evidence, itemPath, fail, true);
    order.push(`${String(item.role)}\0${String(descriptor.name)}\0${String(descriptor.sha256)}`);
    return item;
  });
  sortedUnique(order, path, fail);
  return result;
}

function matchEvidenceRef(raw: unknown, evidence: EvidenceIndex, path: string, fail: Fail, allowRole = false): Record<string, unknown> {
  const ref = record(raw, path, fail);
  exactKeys(ref, allowRole ? ["evidence_name", "role", "sha256"] : ["evidence_name", "sha256"], path, fail);
  const name = identifier(ref.evidence_name, `${path}.evidence_name`, fail);
  if (typeof ref.sha256 !== "string" || !DIGEST_RE.test(ref.sha256)) fail(`${path}.sha256 is invalid`);
  const descriptor = evidence.get(name);
  if (!descriptor || descriptor.sha256 !== ref.sha256) fail(`${path} does not match a body.evidence descriptor`);
  return descriptor;
}

function record(raw: unknown, path: string, fail: Fail): Record<string, unknown> {
  if (!isRecord(raw)) fail(`${path} must be an object`);
  return raw;
}

function array(raw: unknown, path: string, maximum: number, fail: Fail): unknown[] {
  if (!Array.isArray(raw)) fail(`${path} must be an array`);
  if (raw.length > maximum) fail(`${path} exceeds its maximum of ${maximum} items`);
  return raw;
}

function exactKeys(value: Record<string, unknown>, expected: string[], path: string, fail: Fail): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (!sameStringArray(actual, wanted)) fail(`${path} fields do not match profile`);
}

function identifier(raw: unknown, path: string, fail: Fail): string {
  if (typeof raw !== "string" || !IDENTIFIER_RE.test(raw)) fail(`${path} is not a valid identifier`);
  if (SHARED_SECRET_PATTERNS.some((pattern) => pattern.test(raw))) {
    fail(`${path} contains secret-like content`);
  }
  return raw;
}

function rejectSecretStrings(raw: unknown, path: string, fail: Fail): void {
  if (typeof raw === "string") {
    if (SHARED_SECRET_PATTERNS.some((pattern) => pattern.test(raw))) {
      fail(`${path} contains secret-like content`);
    }
    return;
  }
  if (Array.isArray(raw)) {
    raw.forEach((value, index) => rejectSecretStrings(value, `${path}[${index}]`, fail));
    return;
  }
  if (isRecord(raw)) {
    for (const [key, value] of Object.entries(raw)) {
      rejectSecretStrings(key, `${path}.<key>`, fail);
      rejectSecretStrings(value, `${path}.${key}`, fail);
    }
  }
}

function capsuleId(raw: unknown, path: string, fail: Fail): string {
  if (typeof raw !== "string" || !CAPSULE_ID_RE.test(raw)) fail(`${path} is not a valid capsule id`);
  return raw;
}

function positiveSafeInt(raw: unknown, path: string, fail: Fail): number {
  if (!Number.isSafeInteger(raw) || (raw as number) < 1) fail(`${path} must be a positive safe integer`);
  return raw as number;
}

function nonnegativeSafeInt(raw: unknown, path: string, fail: Fail): number {
  if (!Number.isSafeInteger(raw) || (raw as number) < 0) fail(`${path} must be a nonnegative safe integer`);
  return raw as number;
}

function sortedUnique(values: string[], path: string, fail: Fail): void {
  const sorted = [...values].sort();
  if (!sameStringArray(values, sorted) || new Set(values).size !== values.length) fail(`${path} must be unique and canonically sorted`);
}

function sameStringArray(raw: unknown, expected: ReadonlyArray<string>): boolean {
  return Array.isArray(raw) && raw.length === expected.length && raw.every((item, index) => item === expected[index]);
}

function gcd(left: bigint, right: bigint): bigint {
  while (right !== BigInt(0)) [left, right] = [right, left % right];
  return left;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}
