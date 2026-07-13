import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { validateStaticAssets } from "../scripts/validate-static-assets.mjs";

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "unigrok-static-assets-"));
  const publicDirectory = join(root, "public");
  const clientDirectory = join(root, "dist", "client");
  const relativeAsset = join("docs", "okf", "intelligence-payload-semantics-v1.json");
  await mkdir(join(publicDirectory, "docs", "okf"), { recursive: true });
  await mkdir(join(clientDirectory, "docs", "okf"), { recursive: true });
  await writeFile(join(publicDirectory, relativeAsset), '{"version":1}\n');
  await writeFile(join(clientDirectory, relativeAsset), '{"version":1}\n');
  return { root, publicDirectory, clientDirectory, relativeAsset };
}

test("accepts byte-identical public assets", async (t) => {
  const current = await fixture();
  t.after(() => rm(current.root, { recursive: true, force: true }));

  assert.equal(
    await validateStaticAssets(current.publicDirectory, current.clientDirectory),
    1,
  );
});

test("rejects a stale built public asset", async (t) => {
  const current = await fixture();
  t.after(() => rm(current.root, { recursive: true, force: true }));
  await writeFile(join(current.clientDirectory, current.relativeAsset), '{"version":0}\n');

  await assert.rejects(
    validateStaticAssets(current.publicDirectory, current.clientDirectory),
    /Stale built static asset: docs\/okf\/intelligence-payload-semantics-v1\.json/,
  );
});

test("rejects a missing built public asset", async (t) => {
  const current = await fixture();
  t.after(() => rm(current.root, { recursive: true, force: true }));
  await unlink(join(current.clientDirectory, current.relativeAsset));

  await assert.rejects(
    validateStaticAssets(current.publicDirectory, current.clientDirectory),
    /Missing built static asset: docs\/okf\/intelligence-payload-semantics-v1\.json/,
  );
});

test("CLI reports validation failures without a stack trace", async (t) => {
  const current = await fixture();
  t.after(() => rm(current.root, { recursive: true, force: true }));
  await writeFile(join(current.clientDirectory, current.relativeAsset), '{"version":0}\n');

  const result = spawnSync(
    process.execPath,
    [
      fileURLToPath(new URL("../scripts/validate-static-assets.mjs", import.meta.url)),
      current.publicDirectory,
      current.clientDirectory,
    ],
    { encoding: "utf8" },
  );

  assert.equal(result.status, 1);
  assert.equal(result.stdout, "");
  assert.match(
    result.stderr,
    /^Static asset validation failed: Stale built static asset: docs\/okf\/intelligence-payload-semantics-v1\.json\n$/,
  );
});
