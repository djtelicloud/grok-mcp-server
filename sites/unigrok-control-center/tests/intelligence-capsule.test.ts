import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  ACTOR_ROLE_VALUES,
  CAPSULE_KIND_VALUES,
  CapsuleValidationError,
  EXECUTION_PLANE_VALUES,
  EXECUTION_RUNTIME_VALUES,
  EXECUTION_TARGET_VALUES,
  MAX_ENVELOPE_BYTES,
  SIGNATURE_PROFILE_VALUES,
  buildEnvelope,
  canonicalize,
  capsuleId,
  digestBody,
  parseCanonical,
  validateBody,
  validateEnvelopeIntegrity,
} from "../app/lib/intelligence-capsule";

const fixtureUrl = (name: string) =>
  new URL(`../../../tests/fixtures/intelligence_capsule/v1/${name}`, import.meta.url);

async function goldenEnvelope(): Promise<Record<string, unknown>> {
  return JSON.parse(await readFile(fixtureUrl("golden-envelope.json"), "utf8"));
}

async function goldenBytes(): Promise<Uint8Array> {
  const value = (await readFile(fixtureUrl("golden-body.hex"), "utf8")).trim();
  return Uint8Array.from(Buffer.from(value, "hex"));
}

test("TypeScript matches the Python cross-language golden vector", async () => {
  const envelope = await goldenEnvelope();
  const body = envelope.body as Record<string, unknown>;
  const expectedDigest = (await readFile(fixtureUrl("golden-body.sha256"), "utf8")).trim();

  assert.deepEqual(canonicalize(body), await goldenBytes());
  assert.equal(await digestBody(body), expectedDigest);
  assert.equal(await capsuleId(body), `ucap1:sha256:${expectedDigest}`);
  await validateEnvelopeIntegrity(envelope);
});

test("builder preserves body identity when publication signatures change", async () => {
  const original = await goldenEnvelope();
  const body = original.body as Record<string, unknown>;
  const built = await buildEnvelope(body);
  assert.deepEqual(built, original);

  const signed = structuredClone(built);
  signed.signatures = [{ key_id: "admin-key-1", profile: "ssh_ed25519", value: "A".repeat(86) }];
  await validateEnvelopeIntegrity(signed);
  assert.equal(await capsuleId(signed.body as Record<string, unknown>), await capsuleId(body));
});

test("payload Unicode is byte-preserved while metadata must be NFC", async () => {
  const envelope = await goldenEnvelope();
  const body = envelope.body as Record<string, unknown>;
  const payload = body.payload as { data: { decomposed_payload: string } };
  assert.equal(payload.data.decomposed_payload, "e\u0301");
  assert.ok(Buffer.from(canonicalize(body)).includes(Buffer.from("e\u0301")));

  const invalid = structuredClone(body);
  ((invalid.actor as Record<string, unknown>).agent_id) = "cafe\u0301";
  assert.throws(() => validateBody(invalid), /Unicode NFC/);
});

test("semantic profile rejects floats, nulls, unsafe integers, and unsorted sets", async () => {
  const envelope = await goldenEnvelope();
  const cases: Array<[string, (body: Record<string, unknown>) => void]> = [
    ["float", (body) => ((body.payload as { data: Record<string, unknown> }).data.score = 1.5)],
    ["null", (body) => ((body.payload as { data: Record<string, unknown> }).data.optional = null)],
    ["unsafe", (body) => ((body.payload as { data: Record<string, unknown> }).data.unsafe = 2 ** 53)],
    ["unknown", (body) => (body.unknown_field = true)],
    ["parents", (body) => (body.parents as unknown[]).reverse()],
    ["metrics", (body) => (body.metrics as unknown[]).reverse()],
  ];
  for (const [label, mutate] of cases) {
    const body = structuredClone(envelope.body as Record<string, unknown>);
    mutate(body);
    assert.throws(() => validateBody(body), CapsuleValidationError, label);
  }
});

test("raw wire verification rejects alternate JSON spellings", async () => {
  const invalid = [
    '{"a":1,"a":1}',
    '{ "a":1}',
    '{"a":1.0}',
    '{"a":-0}',
  ];
  for (const value of invalid) assert.throws(() => parseCanonical(new TextEncoder().encode(value)));
  assert.throws(() => parseCanonical(Uint8Array.from([0xef, 0xbb, 0xbf, 0x7b, 0x7d])));
  assert.throws(() => parseCanonical(Uint8Array.from([0x7b, 0x22, 0x61, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d])));
});

test("raw wire verification accepts the exact golden body", async () => {
  const envelope = await goldenEnvelope();
  assert.deepEqual(parseCanonical(await goldenBytes()), envelope.body);
});

test("raw wire verification rejects oversized input before JSON decoding", () => {
  assert.throws(() => parseCanonical(new Uint8Array(MAX_ENVELOPE_BYTES + 1)), /1 MiB/);
});

test("year zero is rejected consistently", async () => {
  const envelope = await goldenEnvelope();
  const body = structuredClone(envelope.body as Record<string, unknown>);
  body.created_at = "0000-01-01T00:00:00.000Z";
  assert.throws(() => validateBody(body), /millisecond precision/);
});

test("published schema enums stay coupled to the TypeScript validator", async () => {
  const schema = JSON.parse(await readFile(new URL("../../../docs/okf/intelligence-capsule-v1.schema.json", import.meta.url), "utf8"));
  assert.deepEqual(schema.$defs.body.properties.kind.enum, [...CAPSULE_KIND_VALUES]);
  assert.deepEqual(schema.$defs.actor.properties.role.enum, [...ACTOR_ROLE_VALUES]);
  assert.deepEqual(schema.$defs.execution.properties.runtime.enum, [...EXECUTION_RUNTIME_VALUES]);
  assert.deepEqual(schema.$defs.execution.properties.plane.enum, [...EXECUTION_PLANE_VALUES]);
  assert.deepEqual(schema.$defs.execution.properties.target.enum, [...EXECUTION_TARGET_VALUES]);
  assert.deepEqual(schema.$defs.signature.properties.profile.enum, [...SIGNATURE_PROFILE_VALUES]);
  assert.match(schema.$defs.body.properties.created_at.pattern, /^\^\(\?!0000\)/);
});

test("published validator enum values cannot be mutated by importers", () => {
  for (const values of [
    ACTOR_ROLE_VALUES,
    CAPSULE_KIND_VALUES,
    EXECUTION_PLANE_VALUES,
    EXECUTION_RUNTIME_VALUES,
    EXECUTION_TARGET_VALUES,
    SIGNATURE_PROFILE_VALUES,
  ]) {
    assert.equal(Object.isFrozen(values), true);
    assert.throws(() => (values as unknown as string[]).push("injected"), TypeError);
  }
});
