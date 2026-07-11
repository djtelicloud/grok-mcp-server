import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { binaryAssetFindings, contentFindings, forbiddenFileReason, runSafetyCheck, shouldScanFileContent } from "../scripts/check-template-safety.mjs";

test("rejects personal identifiers and common credential formats", () => {
  const personalEmail = ["owner", "company.invalid"].join("@");
  const bearer = ["Authorization:", "Bearer", "abcdefghijklmnop"].join(" ");
  const privateKey = ["-----BEGIN ENCRYPTED", "PRIVATE KEY-----"].join(" ");
  const awsAccessKey = ["AKIA", "IOSFODNN7EXAMPLE"].join("");
  assert.ok(contentFindings(personalEmail).some((finding) => finding.includes("personal email")));
  assert.ok(contentFindings(bearer).some((finding) => finding.includes("bearer")));
  assert.ok(contentFindings(privateKey).some((finding) => finding.includes("private key")));
  assert.ok(contentFindings(awsAccessKey).some((finding) => finding.includes("AWS")));
});

test("allows reserved examples and blank metadata", () => {
  assert.deepEqual(contentFindings("installer@example.org"), []);
  assert.deepEqual(contentFindings("UNIGROK_TUNNEL_PROFILE=unigrok"), []);
});

test("rejects credential-bearing filenames", () => {
  assert.equal(forbiddenFileReason(".env.example"), null);
  assert.match(forbiddenFileReason(".env") ?? "", /environment/);
  assert.match(forbiddenFileReason("config/.dev.vars") ?? "", /environment/);
  assert.match(forbiddenFileReason("certs/runtime.pem") ?? "", /key file/);
  assert.match(forbiddenFileReason("id_ed25519") ?? "", /SSH/);
});

test("scans text assets and parses PNG metadata separately", () => {
  const personalEmail = ["owner", "company.invalid"].join("@");
  assert.equal(shouldScanFileContent("app/page.tsx"), true);
  assert.equal(shouldScanFileContent("public/favicon.svg"), true);
  assert.equal(shouldScanFileContent("public/og.png"), false);
  assert.deepEqual(binaryAssetFindings("public/fake.png", Buffer.from(personalEmail)), ["invalid PNG binary asset"]);

  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const chunk = (type, data = Buffer.alloc(0)) => {
    const header = Buffer.alloc(8);
    header.writeUInt32BE(data.length, 0);
    header.write(type, 4, 4, "ascii");
    return Buffer.concat([header, data, Buffer.alloc(4)]);
  };
  const pngWithText = Buffer.concat([
    signature,
    chunk("tEXt", Buffer.from(`author\0${personalEmail}`, "latin1")),
    chunk("IEND"),
  ]);
  const findings = binaryAssetFindings("public/metadata.png", pngWithText);
  assert.ok(findings.includes("PNG text metadata is not allowed"));
  assert.ok(findings.includes("personal email address"));
});

test("separates source and provisioned manifest checks", async (context) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "unigrok-safety-"));
  context.after(() => rm(directory, { force: true, recursive: true }));
  await mkdir(path.join(directory, ".openai"), { recursive: true });
  await writeFile(path.join(directory, ".env.example"), "UNIGROK_CONNECTION_MODE=unconfigured\n", "utf8");
  await writeFile(path.join(directory, ".openai", "hosting.json"), JSON.stringify({ d1: null, r2: null }), "utf8");
  const logger = { error() {}, log() {} };

  assert.equal(await runSafetyCheck({ directory, logger, scanTrackedFiles: false }), 0);
  assert.equal(await runSafetyCheck({ allowProvisionedManifest: true, directory, logger, scanTrackedFiles: false }), 1);

  const projectId = ["appgprj", "InstallerOwned123"].join("_");
  await writeFile(path.join(directory, ".openai", "hosting.json"), JSON.stringify({ d1: null, project_id: projectId, r2: null }), "utf8");
  assert.equal(await runSafetyCheck({ directory, logger, scanTrackedFiles: false }), 1);
  assert.equal(await runSafetyCheck({ allowProvisionedManifest: true, directory, logger, scanTrackedFiles: false }), 0);
});

test("rejects nonempty credential metadata with env syntax variants", async (context) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "unigrok-safety-"));
  context.after(() => rm(directory, { force: true, recursive: true }));
  await mkdir(path.join(directory, ".openai"), { recursive: true });
  await writeFile(path.join(directory, ".openai", "hosting.json"), JSON.stringify({ d1: null, r2: null }), "utf8");
  await writeFile(path.join(directory, ".env.example"), " export CLIENT_ID = not-blank\n", "utf8");
  const logger = { error() {}, log() {} };

  assert.equal(await runSafetyCheck({ directory, logger, scanTrackedFiles: false }), 1);
});

test("ignores local env files but rejects them if force-tracked", async (context) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "unigrok-safety-"));
  context.after(() => rm(directory, { force: true, recursive: true }));
  await mkdir(path.join(directory, ".openai"), { recursive: true });
  await writeFile(path.join(directory, ".gitignore"), ".env\n", "utf8");
  await writeFile(path.join(directory, ".env"), "UNIGROK_CONNECTION_MODE=local\n", "utf8");
  await writeFile(path.join(directory, ".env.example"), "UNIGROK_CONNECTION_MODE=unconfigured\n", "utf8");
  await writeFile(path.join(directory, ".openai", "hosting.json"), JSON.stringify({ d1: null, r2: null }), "utf8");
  execFileSync("git", ["init", "-q"], { cwd: directory });
  execFileSync("git", ["add", ".gitignore", ".env.example", ".openai/hosting.json"], { cwd: directory });
  const logger = { error() {}, log() {} };

  assert.equal(await runSafetyCheck({ directory, logger }), 0);
  execFileSync("git", ["add", "-f", ".env"], { cwd: directory });
  assert.equal(await runSafetyCheck({ directory, logger }), 1);
});
