import assert from "node:assert/strict";
import test from "node:test";
import { authorizeGitHubCollaborator } from "../app/lib/github-app";
import { loadGitHubAuthConfig, type GitHubAuthConfig } from "../app/lib/github-auth-config";
import { createGitHubSessionCookie, readGitHubSession } from "../app/lib/github-oauth";

const privateKey = ["-----BEGIN", "PRIVATE KEY-----\n", "A".repeat(256), "\n-----END", "PRIVATE KEY-----"].join(" ").replaceAll("  ", " ");
const environment = {
  APP_BASE_URL: "https://control.grokmcp.org",
  AUTH_SESSION_SECRET: "s".repeat(48),
  GITHUB_APP_CLIENT_ID: "Iv23exampleClient",
  GITHUB_APP_CLIENT_SECRET: "c".repeat(40),
  GITHUB_APP_ID: "4273343",
  GITHUB_APP_INSTALLATION_ID: "12345",
  GITHUB_APP_PRIVATE_KEY: privateKey,
  GITHUB_REPOSITORY: "djtelicloud/grok-mcp-server",
  GITHUB_REPOSITORY_ID: "1295814352",
  NODE_ENV: "production",
} as NodeJS.ProcessEnv;

test("requires and preserves the immutable repository id", () => {
  const config = loadGitHubAuthConfig(environment);
  assert.equal(config.repository.id, 1295814352);
  assert.throws(() => loadGitHubAuthConfig({ ...environment, GITHUB_REPOSITORY_ID: "" }));
  assert.throws(() => loadGitHubAuthConfig({ ...environment, GITHUB_REPOSITORY_ID: "9007199254740992" }));
});

test("session cookies round trip and reject tampering, duplicates, and expiry", async () => {
  const config = loadGitHubAuthConfig(environment);
  const now = 1_800_000_000_000;
  const serialized = await createGitHubSessionCookie(config, { id: 42, login: "contributor" }, now);
  const pair = serialized.split(";", 1)[0];
  assert.equal((await readGitHubSession(config, pair, now + 1_000))?.login, "contributor");
  assert.equal(await readGitHubSession(config, `${pair.slice(0, -1)}x`, now + 1_000), null);
  assert.equal(await readGitHubSession(config, `${pair}; ${pair}`, now + 1_000), null);
  assert.equal(await readGitHubSession(config, pair, now + 3_600_001), null);
});

test("live authorization accepts triage and above but rejects read and identity mismatch", async () => {
  const config = loadGitHubAuthConfig(environment);
  const identity = { id: 42, login: "contributor" };
  const response = (permission: string, id = 42) => async () => Response.json({
    permission,
    user: { id, login: "contributor" },
  });
  assert.equal((await authorizeGitHubCollaborator(config, identity, "t".repeat(40), response("triage")))?.role, "contributor");
  assert.equal((await authorizeGitHubCollaborator(config, identity, "t".repeat(40), response("admin")))?.role, "admin");
  assert.equal(await authorizeGitHubCollaborator(config, identity, "t".repeat(40), response("read")), null);
  assert.equal(await authorizeGitHubCollaborator(config, identity, "t".repeat(40), response("triage", 99)), null);
});

test("a later permission check observes revocation", async () => {
  const config: GitHubAuthConfig = loadGitHubAuthConfig(environment);
  const identity = { id: 42, login: "contributor" };
  let allowed = true;
  const request = async () => allowed
    ? Response.json({ permission: "write", user: identity })
    : new Response("not found", { status: 404 });
  assert.ok(await authorizeGitHubCollaborator(config, identity, "t".repeat(40), request));
  allowed = false;
  assert.equal(await authorizeGitHubCollaborator(config, identity, "t".repeat(40), request), null);
});
