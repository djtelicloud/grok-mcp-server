import { createInstallationCredential, authorizeGitHubCollaborator } from "../../lib/github-app";
import { loadGitHubAuthConfig, requestHostMatchesApplication } from "../../lib/github-auth-config";
import { readGitHubSession } from "../../lib/github-oauth";
import { createAuthorizationCode, loadMcpOAuthConfig, McpOAuthError, normalizeScopes, validateOAuthClient } from "../../lib/mcp-oauth";
import { signCookiePayload, verifyCookiePayload } from "../../lib/signed-cookie";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  try {
    const context = await authorizationContext(request);
    if (!context.session) return loginRedirect(context.url, context.github.appBaseUrl);
    return await consentPage(context.params, context.client.clientName, context.oauth.secret);
  } catch (error) {
    return oauthFailure(error);
  }
}

export async function POST(request: Request): Promise<Response> {
  try {
    const contentType = (request.headers.get("content-type") ?? "").split(";", 1)[0];
    const oauth = loadMcpOAuthConfig();
    if (contentType !== "application/x-www-form-urlencoded" || request.headers.get("origin") !== oauth.issuer) {
      throw new McpOAuthError("invalid_request");
    }
    const body = new URLSearchParams(await request.text());
    const signedConsents = body.getAll("consent");
    if (signedConsents.length !== 1) throw new McpOAuthError("invalid_request");
    const consent = await verifyCookiePayload(signedConsents[0], oauth.secret);
    if (!isConsentPayload(consent, Math.floor(Date.now() / 1_000))) throw new McpOAuthError("invalid_request");
    const approved = consent.params;
    const url = new URL(request.url);
    url.search = new URLSearchParams({
      client_id: approved.clientId,
      code_challenge: approved.challenge,
      code_challenge_method: "S256",
      redirect_uri: approved.redirectUri,
      response_type: "code",
      scope: approved.scope.join(" "),
      state: approved.state,
    }).toString();
    const context = await authorizationContext(new Request(url, { headers: request.headers }));
    if (!context.session) return loginRedirect(context.url, context.github.appBaseUrl);
    const credential = await createInstallationCredential(context.github);
    const authorization = await authorizeGitHubCollaborator(context.github, context.session, credential.token);
    if (!authorization) return new Response("Forbidden", { status: 403, headers: { "cache-control": "no-store" } });
    const code = await createAuthorizationCode(context.oauth, {
      challenge: context.params.challenge,
      clientId: context.params.clientId,
      githubId: context.session.id,
      githubLogin: context.session.login,
      redirectUri: context.params.redirectUri,
      scope: context.params.scope,
    });
    const callback = new URL(context.params.redirectUri);
    callback.searchParams.set("code", code);
    callback.searchParams.set("state", context.params.state);
    console.info(JSON.stringify({ event: "oauth_authorization_granted", actor: context.session.login, scope: context.params.scope }));
    return new Response(null, { status: 302, headers: { location: callback.toString(), "cache-control": "no-store", "referrer-policy": "no-referrer" } });
  } catch (error) {
    return oauthFailure(error);
  }
}

async function authorizationContext(request: Request) {
  const github = loadGitHubAuthConfig();
  const oauth = loadMcpOAuthConfig();
  if (!requestHostMatchesApplication(github, request.headers.get("host"))) throw new McpOAuthError();
  const url = new URL(request.url);
  const params = {
    challenge: one(url, "code_challenge"),
    clientId: one(url, "client_id"),
    redirectUri: one(url, "redirect_uri"),
    scope: normalizeScopes(one(url, "scope")),
    state: one(url, "state"),
  };
  if (one(url, "response_type") !== "code" || one(url, "code_challenge_method") !== "S256" || !params.state || params.state.length > 512) throw new McpOAuthError();
  const client = await validateOAuthClient(oauth, params.clientId, params.redirectUri);
  const session = await readGitHubSession(github, request.headers.get("cookie"));
  return { client, github, oauth, params, session, url };
}

function one(url: URL, key: string): string {
  const values = url.searchParams.getAll(key);
  if (values.length !== 1) throw new McpOAuthError("invalid_request");
  return values[0];
}

function loginRedirect(url: URL, origin: URL): Response {
  const returnTo = `${url.pathname}${url.search}`;
  return Response.redirect(new URL(`/auth/github/login?return_to=${encodeURIComponent(returnTo)}`, origin), 302);
}

async function consentPage(
  params: Awaited<ReturnType<typeof authorizationContext>>["params"],
  clientName: string,
  secret: string,
): Promise<Response> {
  const consent = await signCookiePayload(
    { exp: Math.floor(Date.now() / 1_000) + 300, params, v: 1 },
    secret,
  );
  const hidden = `<input type="hidden" name="consent" value="${escapeHtml(consent)}">`;
  const scopes = params.scope.map((scope) => `<li><code>${escapeHtml(scope)}</code></li>`).join("");
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Authorize UniGrok MCP</title><body><main><h1>Authorize private UniGrok MCP</h1><p><strong>${escapeHtml(clientName)}</strong> at <code>${escapeHtml(new URL(params.redirectUri).origin)}</code> is requesting these project-scoped permissions:</p><ul>${scopes}</ul><p>No GitHub token or xAI credential is sent to the client.</p><form method="post" action="/oauth/authorize">${hidden}<button type="submit">Authorize</button></form></main></body></html>`, {
    headers: { "cache-control": "no-store", "content-type": "text/html; charset=utf-8", "content-security-policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; style-src 'unsafe-inline'", "referrer-policy": "no-referrer", "x-frame-options": "DENY" },
  });
}

function isConsentPayload(
  value: unknown,
  now: number,
): value is {
  exp: number;
  params: Awaited<ReturnType<typeof authorizationContext>>["params"];
  v: 1;
} {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const params = record.params;
  if (!params || typeof params !== "object" || Array.isArray(params)) return false;
  const candidate = params as Record<string, unknown>;
  return (
    record.v === 1 &&
    Number.isSafeInteger(record.exp) &&
    (record.exp as number) > now &&
    (record.exp as number) <= now + 300 &&
    typeof candidate.challenge === "string" &&
    typeof candidate.clientId === "string" &&
    typeof candidate.redirectUri === "string" &&
    typeof candidate.state === "string" &&
    Array.isArray(candidate.scope) &&
    candidate.scope.every((scope) => typeof scope === "string")
  );
}

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function oauthFailure(error: unknown): Response {
  const code = error instanceof McpOAuthError ? error.oauthCode : "server_error";
  return Response.json({ error: code }, { status: code === "temporarily_unavailable" ? 503 : 400, headers: { "cache-control": "no-store" } });
}
