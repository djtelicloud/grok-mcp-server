const PROTOCOL = "org.grokmcp.intelligence-capsule";
const VERSION = 1;
export const MAX_ENVELOPE_BYTES = 1024 * 1024;

export const CAPSULE_KIND_VALUES = Object.freeze([
  "benchmark", "candidate", "decision", "evaluation", "failure", "lesson",
  "observation", "policy", "promotion", "release", "task",
] as const);
export const ACTOR_ROLE_VALUES = Object.freeze(["admin", "automation", "contributor"] as const);
export const EXECUTION_RUNTIME_VALUES = Object.freeze(["cloud", "local"] as const);
export const EXECUTION_PLANE_VALUES = Object.freeze(["api", "cli", "none"] as const);
export const EXECUTION_TARGET_VALUES = Object.freeze(["cloud_api", "deterministic", "local_api", "local_cli"] as const);
export const SIGNATURE_PROFILE_VALUES = Object.freeze(["openpgp", "sigstore_bundle", "ssh_ed25519"] as const);

const CAPSULE_KINDS: ReadonlySet<string> = new Set(CAPSULE_KIND_VALUES);
const ACTOR_ROLES: ReadonlySet<string> = new Set(ACTOR_ROLE_VALUES);
const EXECUTION_RUNTIMES: ReadonlySet<string> = new Set(EXECUTION_RUNTIME_VALUES);
const EXECUTION_PLANES: ReadonlySet<string> = new Set(EXECUTION_PLANE_VALUES);
const EXECUTION_TARGETS: ReadonlySet<string> = new Set(EXECUTION_TARGET_VALUES);
const SIGNATURE_PROFILES: ReadonlySet<string> = new Set(SIGNATURE_PROFILE_VALUES);

const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;
const CAPSULE_ID_RE = /^ucap1:sha256:[a-f0-9]{64}$/;
const COMMIT_RE = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const DECIMAL_RE = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const GIT_OID_RE = /^(?:sha1:[a-f0-9]{40}|sha256:[a-f0-9]{64})$/;
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const MEDIA_TYPE_RE = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/;
const PAYLOAD_SCHEMA_RE = /^org\.grokmcp\.[a-z][a-z0-9_.-]*\.v[1-9][0-9]*$/;
const REPOSITORY_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const TIMESTAMP_RE = /^(?!0000)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$/;
const UUID7_RE = /^[a-f0-9]{8}-[a-f0-9]{4}-7[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const BASE64URL_RE = /^[A-Za-z0-9_-]{16,16384}$/;

export class CapsuleValidationError extends Error {}

export type JsonValue = boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export function canonicalize(value: unknown): Uint8Array {
  const validated = validateCanonicalValue(value, "$", new Set<object>(), { count: 0 }, 0);
  return new TextEncoder().encode(serializeCanonical(validated));
}

export function parseCanonical(raw: Uint8Array): JsonValue {
  if (!(raw instanceof Uint8Array)) throw new CapsuleValidationError("canonical wire input must be a Uint8Array");
  if (raw.byteLength > MAX_ENVELOPE_BYTES) throw new CapsuleValidationError("canonical wire input exceeds the 1 MiB limit");
  if (raw.length >= 3 && raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf) {
    throw new CapsuleValidationError("canonical JSON must not contain a UTF-8 BOM");
  }
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
  } catch {
    throw new CapsuleValidationError("canonical JSON must be strict UTF-8");
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new CapsuleValidationError("invalid canonical JSON");
  }
  const rebuilt = canonicalize(value);
  if (!bytesEqual(raw, rebuilt)) {
    throw new CapsuleValidationError("wire bytes are valid JSON but not canonical");
  }
  return value as JsonValue;
}

export async function digestBody(body: Record<string, unknown>): Promise<string> {
  validateBody(body);
  const canonical = canonicalize(body);
  const input = new ArrayBuffer(canonical.byteLength);
  new Uint8Array(input).set(canonical);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

export async function capsuleId(body: Record<string, unknown>): Promise<string> {
  return `ucap1:sha256:${await digestBody(body)}`;
}

export async function buildEnvelope(
  body: Record<string, unknown>,
  signatures: Array<Record<string, unknown>> = [],
): Promise<Record<string, unknown>> {
  const envelope = {
    body,
    digest: { algorithm: "sha-256", value: await digestBody(body) },
    signatures,
  };
  await validateEnvelopeIntegrity(envelope);
  return envelope;
}

export async function validateEnvelopeIntegrity(value: unknown): Promise<void> {
  const envelope = requireRecord(value, "$", ["body", "digest", "signatures"]);
  const body = requireRecord(envelope.body, "$.body");
  validateBody(body);
  const digest = requireRecord(envelope.digest, "$.digest", ["algorithm", "value"]);
  if (digest.algorithm !== "sha-256" || !matches(digest.value, DIGEST_RE)) {
    throw new CapsuleValidationError("$.digest must contain a lowercase sha-256 value");
  }
  if (digest.value !== await digestBody(body)) {
    throw new CapsuleValidationError("$.digest.value does not match the canonical body");
  }
  const signatures = requireArray(envelope.signatures, "$.signatures", 32);
  const order: string[] = [];
  signatures.forEach((raw, index) => {
    const path = `$.signatures[${index}]`;
    const signature = requireRecord(raw, path, ["key_id", "profile", "value"]);
    if (typeof signature.profile !== "string" || !SIGNATURE_PROFILES.has(signature.profile)) {
      throw new CapsuleValidationError(`${path}.profile is unsupported`);
    }
    if (!matches(signature.key_id, IDENTIFIER_RE)) throw new CapsuleValidationError(`${path}.key_id is invalid`);
    if (!matches(signature.value, BASE64URL_RE)) throw new CapsuleValidationError(`${path}.value is not unpadded base64url`);
    order.push(`${signature.profile}\0${signature.key_id}\0${signature.value}`);
  });
  requireSortedUnique(order, "$.signatures");
  validateCanonicalValue(envelope, "$", new Set<object>(), { count: 0 }, 0);
  if (canonicalize(envelope).byteLength > MAX_ENVELOPE_BYTES) throw new CapsuleValidationError("envelope exceeds the 1 MiB canonical limit");
}

export function validateBody(value: unknown): asserts value is Record<string, unknown> {
  const required = [
    "actor", "created_at", "evidence", "kind", "parents", "payload", "protocol",
    "provenance", "run_id", "subject", "version",
  ];
  const body = requireRecord(value, "$.body", required, ["execution", "metrics"]);
  if (body.protocol !== PROTOCOL || body.version !== VERSION) {
    throw new CapsuleValidationError("$.body protocol/version is not IntelligenceCapsule v1");
  }
  if (typeof body.kind !== "string" || !CAPSULE_KINDS.has(body.kind)) throw new CapsuleValidationError("$.body.kind is unsupported");
  if (!matches(body.run_id, UUID7_RE)) throw new CapsuleValidationError("$.body.run_id must be a lowercase UUIDv7");
  if (!matches(body.created_at, TIMESTAMP_RE)) throw new CapsuleValidationError("$.body.created_at must use UTC millisecond precision");
  try {
    if (new Date(body.created_at as string).toISOString() !== body.created_at) throw new Error("normalized");
  } catch {
    throw new CapsuleValidationError("$.body.created_at is not a real UTC timestamp");
  }
  requireNfc(body.created_at, "$.body.created_at");

  const subject = requireRecord(body.subject, "$.body.subject", ["commit", "repository"]);
  if (!matches(subject.repository, REPOSITORY_RE)) throw new CapsuleValidationError("$.body.subject.repository is invalid");
  if (!matches(subject.commit, COMMIT_RE)) throw new CapsuleValidationError("$.body.subject.commit must be a full Git object id");
  requireNfc(subject.repository, "$.body.subject.repository");

  const parents = requireArray(body.parents, "$.body.parents", 64);
  if (!parents.every((item) => matches(item, CAPSULE_ID_RE))) throw new CapsuleValidationError("$.body.parents contains an invalid capsule id");
  requireSortedUnique(parents as string[], "$.body.parents");

  const actor = requireRecord(body.actor, "$.body.actor", ["agent_id", "github_login", "role"]);
  requireNfc(actor.agent_id, "$.body.actor.agent_id");
  requireNfc(actor.github_login, "$.body.actor.github_login");
  if (!matches(actor.agent_id, IDENTIFIER_RE)) throw new CapsuleValidationError("$.body.actor.agent_id is invalid");
  if (!matches(actor.github_login, IDENTIFIER_RE)) throw new CapsuleValidationError("$.body.actor.github_login is invalid");
  if (typeof actor.role !== "string" || !ACTOR_ROLES.has(actor.role)) throw new CapsuleValidationError("$.body.actor.role is invalid");

  const payload = requireRecord(body.payload, "$.body.payload", ["data", "schema"]);
  if (!matches(payload.schema, PAYLOAD_SCHEMA_RE)) throw new CapsuleValidationError("$.body.payload.schema is invalid");
  requireNfc(payload.schema, "$.body.payload.schema");
  requireRecord(payload.data, "$.body.payload.data");

  const evidence = requireArray(body.evidence, "$.body.evidence", 256);
  const evidenceOrder: string[] = [];
  evidence.forEach((raw, index) => {
    const path = `$.body.evidence[${index}]`;
    const item = requireRecord(raw, path, ["bytes", "media_type", "name", "sha256"], ["git_oid"]);
    if (!matches(item.name, IDENTIFIER_RE)) throw new CapsuleValidationError(`${path}.name is invalid`);
    if (!matches(item.media_type, MEDIA_TYPE_RE)) throw new CapsuleValidationError(`${path}.media_type is invalid`);
    if (!Number.isSafeInteger(item.bytes) || (item.bytes as number) < 0) throw new CapsuleValidationError(`${path}.bytes is invalid`);
    if (!matches(item.sha256, DIGEST_RE)) throw new CapsuleValidationError(`${path}.sha256 is invalid`);
    if (item.git_oid !== undefined && !matches(item.git_oid, GIT_OID_RE)) throw new CapsuleValidationError(`${path}.git_oid is invalid`);
    requireNfc(item.name, `${path}.name`);
    requireNfc(item.media_type, `${path}.media_type`);
    evidenceOrder.push(`${item.name}\0${item.sha256}`);
  });
  requireSortedUnique(evidenceOrder, "$.body.evidence");

  const provenance = requireRecord(body.provenance, "$.body.provenance", ["generator", "generator_version", "source_commit"]);
  if (!matches(provenance.generator, IDENTIFIER_RE)) throw new CapsuleValidationError("$.body.provenance.generator is invalid");
  if (!matches(provenance.generator_version, IDENTIFIER_RE)) throw new CapsuleValidationError("$.body.provenance.generator_version is invalid");
  if (!matches(provenance.source_commit, COMMIT_RE)) throw new CapsuleValidationError("$.body.provenance.source_commit is invalid");
  requireNfc(provenance.generator, "$.body.provenance.generator");
  requireNfc(provenance.generator_version, "$.body.provenance.generator_version");

  if (body.execution !== undefined) {
    const execution = requireRecord(body.execution, "$.body.execution", ["model", "plane", "runtime", "target"]);
    if (!matches(execution.model, IDENTIFIER_RE)) throw new CapsuleValidationError("$.body.execution.model is invalid");
    if (typeof execution.runtime !== "string" || !EXECUTION_RUNTIMES.has(execution.runtime)) throw new CapsuleValidationError("$.body.execution.runtime is invalid");
    if (typeof execution.plane !== "string" || !EXECUTION_PLANES.has(execution.plane)) throw new CapsuleValidationError("$.body.execution.plane is invalid");
    if (typeof execution.target !== "string" || !EXECUTION_TARGETS.has(execution.target)) throw new CapsuleValidationError("$.body.execution.target is invalid");
    requireNfc(execution.model, "$.body.execution.model");
  }

  const metrics = body.metrics === undefined ? [] : requireArray(body.metrics, "$.body.metrics", 256);
  const metricOrder: string[] = [];
  metrics.forEach((raw, index) => {
    const path = `$.body.metrics[${index}]`;
    const metric = requireRecord(raw, path, ["name", "unit", "value"]);
    if (!matches(metric.name, IDENTIFIER_RE)) throw new CapsuleValidationError(`${path}.name is invalid`);
    if (!matches(metric.unit, IDENTIFIER_RE)) throw new CapsuleValidationError(`${path}.unit is invalid`);
    if (!matches(metric.value, DECIMAL_RE)) throw new CapsuleValidationError(`${path}.value must be a plain decimal string`);
    requireNfc(metric.name, `${path}.name`);
    requireNfc(metric.unit, `${path}.unit`);
    metricOrder.push(`${metric.name}\0${metric.unit}`);
  });
  requireSortedUnique(metricOrder, "$.body.metrics");

  validateCanonicalValue(body, "$.body", new Set<object>(), { count: 0 }, 0);
  if (canonicalize(body).byteLength > 256 * 1024) throw new CapsuleValidationError("$.body exceeds the 256 KiB canonical limit");
}

function validateCanonicalValue(value: unknown, path: string, seen: Set<object>, nodes: { count: number }, depth: number): JsonValue {
  if (depth > 64) throw new CapsuleValidationError(`${path} exceeds the maximum nesting depth`);
  nodes.count += 1;
  if (nodes.count > 100_000) throw new CapsuleValidationError("canonical value exceeds the maximum node count");
  if (value === null) throw new CapsuleValidationError(`${path} must omit inapplicable fields, not use null`);
  if (typeof value === "boolean" || typeof value === "string") {
    if (typeof value === "string") rejectLoneSurrogates(value, path);
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) throw new CapsuleValidationError(`${path} must be a safe integer other than negative zero, or a decimal string`);
    return value;
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new CapsuleValidationError(`${path} contains a reference cycle`);
    if (Object.keys(value).length !== value.length) throw new CapsuleValidationError(`${path} must be a dense array`);
    seen.add(value);
    try { return value.map((item, index) => validateCanonicalValue(item, `${path}[${index}]`, seen, nodes, depth + 1)); }
    finally { seen.delete(value); }
  }
  if (!isPlainRecord(value)) throw new CapsuleValidationError(`${path} contains an unsupported value`);
  if (seen.has(value)) throw new CapsuleValidationError(`${path} contains a reference cycle`);
  if (Reflect.ownKeys(value).length !== Object.keys(value).length) throw new CapsuleValidationError(`${path} contains a symbol or hidden key`);
  seen.add(value);
  const output: Record<string, JsonValue> = {};
  try {
    for (const key of Object.keys(value)) {
      if (!KEY_RE.test(key)) throw new CapsuleValidationError(`${path} contains a non-canonical object key`);
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || descriptor.get || descriptor.set || !descriptor.enumerable) throw new CapsuleValidationError(`${path}.${key} is not a plain enumerable value`);
      output[key] = validateCanonicalValue(value[key], `${path}.${key}`, seen, nodes, depth + 1);
    }
    return output;
  } finally { seen.delete(value); }
}

function serializeCanonical(value: JsonValue): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return quoteCanonicalString(value);
  if (Array.isArray(value)) return `[${value.map(serializeCanonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${quoteCanonicalString(key)}:${serializeCanonical(value[key])}`).join(",")}}`;
}

function quoteCanonicalString(value: string): string {
  const shortEscapes = new Map<number, string>([
    [0x08, "\\b"], [0x09, "\\t"], [0x0a, "\\n"], [0x0c, "\\f"], [0x0d, "\\r"],
  ]);
  let output = '"';
  for (const character of value) {
    const codepoint = character.codePointAt(0)!;
    if (character === '"') output += '\\"';
    else if (character === "\\") output += "\\\\";
    else if (shortEscapes.has(codepoint)) output += shortEscapes.get(codepoint);
    else if (codepoint <= 0x1f) output += `\\u${codepoint.toString(16).padStart(4, "0")}`;
    else output += character;
  }
  return `${output}"`;
}

function requireRecord(value: unknown, path: string, required: string[] = [], optional: string[] = []): Record<string, unknown> {
  if (!isPlainRecord(value)) throw new CapsuleValidationError(`${path} must be an object`);
  const keys = Object.keys(value);
  const missing = required.filter((key) => !keys.includes(key));
  const allowed = new Set([...required, ...optional]);
  const extra = allowed.size ? keys.filter((key) => !allowed.has(key)) : [];
  if (missing.length) throw new CapsuleValidationError(`${path} is missing ${missing.sort().join(",")}`);
  if (extra.length) throw new CapsuleValidationError(`${path} contains unsupported fields ${extra.sort().join(",")}`);
  return value;
}

function requireArray(value: unknown, path: string, maximum: number): unknown[] {
  if (!Array.isArray(value)) throw new CapsuleValidationError(`${path} must be an array`);
  if (value.length > maximum) throw new CapsuleValidationError(`${path} exceeds its maximum of ${maximum} items`);
  return value;
}

function requireSortedUnique(values: string[], path: string): void {
  const sorted = [...values].sort();
  if (values.some((value, index) => value !== sorted[index]) || new Set(values).size !== values.length) {
    throw new CapsuleValidationError(`${path} must be unique and canonically sorted`);
  }
}

function requireNfc(value: unknown, path: string): void {
  if (typeof value !== "string" || value.normalize("NFC") !== value) throw new CapsuleValidationError(`${path} must be Unicode NFC`);
}

function matches(value: unknown, expression: RegExp): value is string { return typeof value === "string" && expression.test(value); }
function isPlainRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype; }
function bytesEqual(left: Uint8Array, right: Uint8Array): boolean { return left.length === right.length && left.every((value, index) => value === right[index]); }

function rejectLoneSurrogates(value: string, path: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new CapsuleValidationError(`${path} contains a lone UTF-16 surrogate`);
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new CapsuleValidationError(`${path} contains a lone UTF-16 surrogate`);
    }
  }
}
