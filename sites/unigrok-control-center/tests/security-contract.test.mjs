import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("ships a structurally valid Sites manifest", async () => {
  const manifest = JSON.parse(await readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"));
  assert.equal(manifest.d1, null);
  assert.equal(manifest.r2, null);
  if (Object.hasOwn(manifest, "project_id")) {
    assert.deepEqual(Object.keys(manifest).sort(), ["d1", "project_id", "r2"]);
    const projectIdPattern = new RegExp(`^${["appgprj", "_"].join("")}[A-Za-z0-9]+$`);
    assert.match(manifest.project_id, projectIdPattern);
  } else {
    assert.deepEqual(manifest, { d1: null, r2: null });
  }
});

test("requires server-side ChatGPT identity", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /requireChatGPTUser\("\/"\)/);
  assert.match(page, /dynamic = "force-dynamic"/);
  assert.doesNotMatch(page, /user\.email/);
});

test("keeps the connection wizard instructional", async () => {
  const controlCenter = await readFile(new URL("../app/control-center.tsx", import.meta.url), "utf8");
  assert.match(controlCenter, /A deployed Site cannot reach your laptop through localhost/);
  assert.match(controlCenter, /tunnel-client doctor --profile/);
  assert.match(controlCenter, /aria-controls="connection-panel-local"/);
  assert.match(controlCenter, /role="tabpanel"/);
  assert.match(controlCenter, /event\.key === "ArrowLeft"/);
  assert.doesNotMatch(controlCenter, /fetch\(/);
  assert.doesNotMatch(controlCenter, /localStorage|sessionStorage|dangerouslySetInnerHTML/);
});

test("requires adapters to separate PR review state from release impact", async () => {
  const contract = await readFile(new URL("../app/lib/control-center-contract.ts", import.meta.url), "utf8");
  const controlCenter = await readFile(new URL("../app/control-center.tsx", import.meta.url), "utf8");

  assert.match(contract, /releaseImpact: "blocking" \| "informational"/);
  assert.match(contract, /item\.releaseImpact === "blocking"/);
  assert.doesNotMatch(contract, /reviewState === "changes_requested"/);
  assert.match(controlCenter, /Changes requested affects that PR only/);
  assert.match(controlCenter, /Informational to release/);
});

test("defines no credential-bearing environment variable", async () => {
  const envExample = await readFile(new URL("../.env.example", import.meta.url), "utf8");
  assert.doesNotMatch(envExample, /(?:TOKEN|SECRET|PASSWORD|API_KEY)=/);
  assert.doesNotMatch(envExample, /NEXT_PUBLIC_/);
});
