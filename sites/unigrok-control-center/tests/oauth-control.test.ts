import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import test from "node:test";
import type { GitHubAuthConfig } from "../app/lib/github-auth-config";
import { fetchImmutableReviewEvidence } from "../app/lib/github-review-broker";
import { GET as authorizeGet, POST as authorizePost } from "../app/oauth/authorize/route";
import { createGitHubSessionCookie } from "../app/lib/github-oauth";
import { loadGitHubAuthConfig } from "../app/lib/github-auth-config";
import {
  buildLandingReceiptPayload,
  loadReceiptSigningConfig,
  signLandingReceipt,
  verifyLandingReceipt,
} from "../app/lib/landing-receipt";
import {
  createAuthorizationCode,
  createServiceAccessToken,
  exchangeAuthorizationCode,
  McpOAuthError,
  normalizeScopes,
  readAccessToken,
  registerOAuthClient,
  type McpOAuthConfig,
} from "../app/lib/mcp-oauth";
import { sha256Base64Url, signCookiePayload } from "../app/lib/signed-cookie";

const oauth: McpOAuthConfig = {
  issuer: "https://control.grokmcp.org",
  resource: "https://mcp.grokmcp.org/mcp",
  secret: "s".repeat(48),
};

const github = {
  repository: { id: 1295814352, name: "grok-mcp-server", owner: "djtelicloud" },
} as GitHubAuthConfig;

test("dynamic OAuth client, PKCE code, and scoped token round trip", async () => {
  const registration = await registerOAuthClient(oauth, {
    client_name: "Local MCP client",
    redirect_uris: ["http://127.0.0.1:4567/callback"],
  });
  const verifier = "v".repeat(64);
  const code = await createAuthorizationCode(oauth, {
    challenge: await sha256Base64Url(verifier),
    clientId: registration.client_id,
    githubId: 42,
    githubLogin: "contributor",
    redirectUri: registration.redirect_uris[0],
    scope: normalizeScopes("unigrok:connect unigrok:review"),
  }, 1_800_000_000_000);
  const input = {
    clientId: registration.client_id,
    code,
    redirectUri: registration.redirect_uris[0],
    verifier,
  };
  const first = await exchangeAuthorizationCode(oauth, input, 1_800_000_001_000);
  const replay = await exchangeAuthorizationCode(oauth, input, 1_800_000_001_000);
  assert.equal(first.access_token, replay.access_token);
  assert.equal(first.scope, "unigrok:connect unigrok:review");
  const claims = await readAccessToken(oauth, first.access_token, 1_800_000_001_000);
  assert.equal(claims?.sub, "github:42");
  assert.deepEqual(claims?.scope, ["unigrok:connect", "unigrok:review"]);
  await assert.rejects(() => exchangeAuthorizationCode(oauth, { ...input, verifier: "x".repeat(64) }, 1_800_000_001_000));
  assert.equal(await readAccessToken(oauth, first.access_token, 1_800_000_800_000), null);
  assert.throws(() => normalizeScopes("unigrok:review repository:write"));
});

test("cursor-cloud service tokens require the exact fixed scope bundle", async () => {
  const now = 1_800_000_000_000;
  const token = await createServiceAccessToken(
    oauth,
    "cursor-cloud",
    "unigrok:invoke",
    now,
  );
  const claims = await readAccessToken(oauth, token, now + 1_000);
  assert.equal(claims?.sub, "service:cursor-cloud");
  assert.deepEqual(claims?.scope, [
    "unigrok:connect",
    "unigrok:invoke",
    "unigrok:status",
  ]);
  await assert.rejects(
    () => createServiceAccessToken(oauth, "cursor-cloud", "unigrok:status", now),
    (error: unknown) =>
      error instanceof McpOAuthError && error.oauthCode === "invalid_scope",
  );

  const iat = Math.floor(now / 1_000);
  const missingStatus = {
    aud: oauth.resource,
    exp: iat + 600,
    iat,
    iss: oauth.issuer,
    jti: "missing-status-scope-test",
    kind: "service",
    scope: ["unigrok:connect", "unigrok:invoke"],
    sub: "service:cursor-cloud",
    v: 1,
  };
  const malformed = `ugtoken.${await signCookiePayload(missingStatus, oauth.secret)}`;
  assert.equal(await readAccessToken(oauth, malformed, now + 1_000), null);
});

test("authorization requires same-origin explicit consent before issuing a code", async () => {
  const rsa = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const environment = {
    APP_BASE_URL: oauth.issuer,
    AUTH_SESSION_SECRET: "a".repeat(48),
    CONTROL_CENTER_MODE: "github",
    GITHUB_APP_CLIENT_ID: "Iv23exampleClient",
    GITHUB_APP_CLIENT_SECRET: "c".repeat(40),
    GITHUB_APP_ID: "4273343",
    GITHUB_APP_INSTALLATION_ID: "145922568",
    GITHUB_APP_PRIVATE_KEY: rsa.privateKey.export({ format: "pem", type: "pkcs8" }).toString(),
    GITHUB_REPOSITORY: "djtelicloud/grok-mcp-server",
    GITHUB_REPOSITORY_ID: "1295814352",
    MCP_RESOURCE_URL: oauth.resource,
    MCP_TOKEN_SECRET: oauth.secret,
    NODE_ENV: "production",
  };
  const previous = new Map(Object.keys(environment).map((key) => [key, process.env[key]]));
  Object.assign(process.env, environment);
  try {
    const registration = await registerOAuthClient(oauth, {
      client_name: "Consent test",
      redirect_uris: ["http://127.0.0.1:4567/callback"],
    });
    const githubConfig = loadGitHubAuthConfig();
    const cookie = (await createGitHubSessionCookie(githubConfig, { id: 42, login: "contributor" })).split(";", 1)[0];
    const verifier = "v".repeat(64);
    const url = new URL("https://control.grokmcp.org/oauth/authorize");
    url.search = new URLSearchParams({
      client_id: registration.client_id,
      code_challenge: await sha256Base64Url(verifier),
      code_challenge_method: "S256",
      redirect_uri: registration.redirect_uris[0],
      response_type: "code",
      scope: "unigrok:connect unigrok:review",
      state: "state-value",
    }).toString();
    const consent = await authorizeGet(new Request(url, { headers: { cookie, host: "control.grokmcp.org" } }));
    assert.equal(consent.status, 200);
    const html = await consent.text();
    assert.match(html, /Authorize private UniGrok MCP/);
    assert.match(html, /unigrok:review/);
    assert.doesNotMatch(html, /ugcode\./);
    const crossSite = await authorizePost(new Request("https://control.grokmcp.org/oauth/authorize", {
      body: new URLSearchParams(url.searchParams),
      headers: { "content-type": "application/x-www-form-urlencoded", cookie, host: "control.grokmcp.org", origin: "https://evil.example" },
      method: "POST",
    }));
    assert.equal(crossSite.status, 400);
  } finally {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test("review broker binds immutable base and head and refuses a force-push race", async () => {
  const base = "b".repeat(40);
  const head = "a".repeat(40);
  const changed = "c".repeat(40);
  let pullReads = 0;
  const request = async (url: string | URL | Request) => {
    const path = String(url);
    if (path.includes("/compare/")) return new Response("diff --git a/a b/a");
    pullReads += 1;
    return Response.json({
      title: "PR",
      base: { sha: base },
      head: { sha: pullReads === 1 ? head : changed },
    });
  };
  await assert.rejects(
    () => fetchImmutableReviewEvidence(github, "t".repeat(40), 7, head, request as typeof fetch),
    /changed while review evidence was collected/,
  );
});

test("Ed25519 receipts verify offline and altered payloads fail", () => {
  const pair = generateKeyPairSync("ed25519");
  const config = loadReceiptSigningConfig({
    RECEIPT_SIGNING_KEY_ID: "test-2026-07",
    RECEIPT_SIGNING_PRIVATE_KEY: pair.privateKey.export({ format: "pem", type: "pkcs8" }).toString(),
    RECEIPT_SIGNING_PUBLIC_KEY: pair.publicKey.export({ format: "pem", type: "spki" }).toString(),
  } as unknown as NodeJS.ProcessEnv);
  const payload = {
    actor: "maintainer",
    base_sha: "b".repeat(40),
    broker_version: "unigrok-control-center-v1",
    codex_disposition: { check_id: 17, name: "Codex approval gate" },
    head_sha: "a".repeat(40),
    issued_at: "2026-07-12T00:00:00.000Z",
    merge_commit_sha: "c".repeat(40),
    policy_version: "cloud-control-v1",
    pull_number: 9,
    repository: "djtelicloud/grok-mcp-server",
    required_checks: [{ conclusion: "success", name: "Codex approval gate" }],
    resulting_main_sha: "d".repeat(40),
    schema_version: "unigrok-signed-landing-receipt-v1",
  } satisfies import("../app/lib/landing-receipt").LandingReceiptPayload;
  const receipt = signLandingReceipt(config, payload);
  assert.equal(verifyLandingReceipt(config, receipt), true);
  const altered = { ...receipt, payload: { ...receipt.payload, pull_number: 10 } };
  assert.equal(verifyLandingReceipt(config, altered), false);
});

test("landing evidence refuses a stale expected head before signing", async () => {
  const actualHead = "a".repeat(40);
  const request = async (url: string | URL | Request) => {
    assert.match(String(url), /\/pulls\/3$/);
    return Response.json({
      head: { sha: actualHead },
      base: { sha: "b".repeat(40) },
      merge_commit_sha: "c".repeat(40),
      merged_at: "2026-07-12T00:00:00Z",
    });
  };
  await assert.rejects(
    () => buildLandingReceiptPayload(github, "t".repeat(40), { actor: "maintainer", expectedHeadSha: "f".repeat(40), pullNumber: 3 }, request as typeof fetch),
    /head is stale/,
  );
});
