import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readProjectFile = (relativePath) =>
  readFile(new URL(`../${relativePath}`, import.meta.url), "utf8");

test("keeps the standalone build separate from the Sites build", async () => {
  const packageDocument = JSON.parse(await readProjectFile("package.json"));
  const nextConfig = await readProjectFile("next.config.ts");
  const buildScript = await readProjectFile("scripts/build-standalone.sh");

  assert.equal(packageDocument.scripts.build, "bash scripts/build-verified.sh");
  assert.equal(
    packageDocument.scripts["build:standalone"],
    "bash scripts/build-standalone.sh",
  );
  assert.match(nextConfig, /UNIGROK_BUILD_TARGET === "standalone"/);
  assert.match(nextConfig, /output: "standalone"/);
  assert.match(buildScript, /GITHUB_APP_PRIVATE_KEY/);
  assert.match(buildScript, /GITHUB_APP_CLIENT_SECRET/);
  assert.match(buildScript, /AUTH_SESSION_SECRET/);
  assert.match(buildScript, /Refusing a standalone build/);
});

test("builds an unprivileged image without credential inputs", async () => {
  const dockerfile = await readProjectFile("Dockerfile.cloudrun");
  const dockerignore = await readProjectFile(".dockerignore");
  const entrypoint = await readProjectFile("scripts/cloudrun-entrypoint.sh");

  assert.match(dockerfile, /USER 10001:10001/);
  assert.match(dockerfile, /node:22\.22\.0-bookworm-slim@sha256:[a-f0-9]{64}/);
  assert.match(dockerfile, /CMD \["\/app\/cloudrun-entrypoint\.sh"\]/);
  assert.match(dockerfile, /\/api\/public\/v1\/project/);
  assert.doesNotMatch(dockerfile, /ARG\s+.*(?:SECRET|PRIVATE_KEY|TOKEN)/i);
  assert.doesNotMatch(
    dockerfile,
    /ENV\s+.*(?:GITHUB_APP_PRIVATE_KEY|GITHUB_APP_CLIENT_SECRET|AUTH_SESSION_SECRET)/,
  );

  assert.match(dockerignore, /^\.env$/m);
  assert.match(dockerignore, /^\.env\.\*$/m);
  assert.match(dockerignore, /^\*\.pem$/m);
  assert.match(dockerignore, /^\*\.key$/m);

  assert.match(entrypoint, /CONTROL_CENTER_MODE:-.*github/);
  assert.match(entrypoint, /AUTH_SESSION_SECRET/);
  assert.match(entrypoint, /GITHUB_REPOSITORY_ID/);
  assert.match(entrypoint, /exit 78/);
  assert.match(entrypoint, /exec node server\.js/);
});

test("documents exact-origin auth, versioned secrets, rollback, and domain cutover", async () => {
  const deployment = await readProjectFile("docs/cloud-run-deployment.md");

  assert.match(deployment, /https:\/\/control\.grokmcp\.org\/auth\/github\/callback/);
  assert.match(deployment, /Pin an explicit Secret Manager version/);
  assert.match(deployment, /route `100%` of traffic back/);
  assert.match(deployment, /internal-and-cloud-load-balancing/);
  assert.match(deployment, /Do not add a permissive CORS policy/);
  assert.match(deployment, /Cloud CDN disabled/i);
  assert.match(deployment, /Cloud Armor[\s\S]*preview/i);
});
