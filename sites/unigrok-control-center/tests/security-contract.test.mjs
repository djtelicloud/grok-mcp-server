import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("binds source to a structurally valid Sites project", async () => {
  const manifest = JSON.parse(await readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"));
  assert.deepEqual(Object.keys(manifest).sort(), ["d1", "project_id", "r2"]);
  assert.equal(manifest.d1, null);
  assert.equal(manifest.r2, null);
  const projectIdPattern = new RegExp(`^${["appgprj", "_"].join("")}[A-Za-z0-9]+$`);
  assert.match(manifest.project_id, projectIdPattern);
});

test("keeps the root public and protects control server-side", async () => {
  const publicPage = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const controlPage = await readFile(new URL("../app/control/page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(publicPage, /requireChatGPTUser/);
  assert.match(publicPage, /Public project context/);
  assert.match(controlPage, /requireChatGPTUser\("\/control"\)/);
  assert.match(controlPage, /getGitHubProjectAuthorization\(user\.email\)/);
  assert.match(controlPage, /if \(!authorization\.authorized\)/);
  assert.match(controlPage, /dynamic = "force-dynamic"/);
  assert.doesNotMatch(controlPage, /user\.email[},<]/);
});

test("legacy Sites authorization fails closed and points to canonical live verification", async () => {
  const authorization = await readFile(new URL("../app/lib/github-project-authorization.ts", import.meta.url), "utf8");
  const denied = await readFile(new URL("../app/control/access-denied.tsx", import.meta.url), "utf8");
  const controlCenter = await readFile(new URL("../app/control-center.tsx", import.meta.url), "utf8");

  assert.match(authorization, /if \(!raw\) return \{ authorized: false, reason: "not-configured" \}/);
  assert.match(authorization, /invalid-configuration/);
  assert.match(authorization, /not-authorized/);
  assert.doesNotMatch(authorization, /fetch\(|github\.com\/login\/oauth/);
  assert.match(denied, /canonical control origin uses GitHub OAuth/);
  assert.match(controlCenter, /canonical control origin performs live GitHub OAuth/);
});

test("publishes three public-safe machine-readable routes", async () => {
  const projectRoute = await readFile(new URL("../app/api/public/v1/project/route.ts", import.meta.url), "utf8");
  const discoveryRoute = await readFile(new URL("../app/.well-known/unigrok.json/route.ts", import.meta.url), "utf8");
  const llmsRoute = await readFile(new URL("../app/llms.txt/route.ts", import.meta.url), "utf8");
  const publicProject = await readFile(new URL("../app/lib/public-project.ts", import.meta.url), "utf8");

  assert.match(projectRoute, /publicProjectDocument/);
  assert.match(discoveryRoute, /publicDiscoveryDocument/);
  assert.match(llmsRoute, /publicLlmsText/);
  assert.match(publicProject, /private-oauth-api-plane/);
  assert.doesNotMatch(publicProject, /runtime ready|UNIGROK_GITHUB_IDENTITY_BINDINGS/);
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

test("publishes Swarm as a client-only showcase", async () => {
  const publicPage = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const swarmScript = await readFile(new URL("../../../mcp_ui/swarm.js", import.meta.url), "utf8");
  const syncScript = await readFile(new URL("../scripts/sync-swarm-playground.mjs", import.meta.url), "utf8");

  assert.match(publicPage, /href="\/swarm\/"/);
  assert.match(swarmScript, /state\.runtimeMode === "contributor"/);
  assert.match(swarmScript, /source was not uploaded/);
  assert.match(swarmScript, /Public showcase — client-side analysis only/);
  assert.doesNotMatch(swarmScript, /localStorage|sessionStorage/);
  assert.match(syncScript, /mcp_ui/);
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

test("defines no browser-exposed or committed credential value", async () => {
  const envExample = await readFile(new URL("../.env.example", import.meta.url), "utf8");
  assert.match(envExample, /^UNIGROK_GITHUB_IDENTITY_BINDINGS=$/m);
  for (const line of envExample.split(/\r?\n/)) {
    if (/(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)=/.test(line)) assert.match(line, /=$/);
  }
  assert.doesNotMatch(envExample, /NEXT_PUBLIC_/);
});
