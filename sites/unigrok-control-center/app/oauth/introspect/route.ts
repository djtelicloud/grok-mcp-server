import { authorizeGitHubCollaborator, createInstallationCredential } from "../../lib/github-app";
import { loadGitHubAuthConfig } from "../../lib/github-auth-config";
import { loadMcpOAuthConfig, readAccessToken } from "../../lib/mcp-oauth";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  let reason = "invalid_token";
  try {
    const token = bearer(request.headers.get("authorization"));
    const form = new URLSearchParams(await request.text());
    const requiredScope = form.get("required_scope") ?? "";
    const requiredScopes = requiredScope.split(/\s+/u).filter(Boolean);
    const oauth = loadMcpOAuthConfig();
    const claims = token ? await readAccessToken(oauth, token) : null;
    if (!claims) return inactive(reason);
    if (requiredScopes.some((scope) => !claims.scope.includes(scope))) return inactive("insufficient_scope");
    if (claims.kind === "user") {
      const github = loadGitHubAuthConfig();
      const credential = await createInstallationCredential(github);
      const authorization = await authorizeGitHubCollaborator(github, { id: claims.githubId!, login: claims.githubLogin! }, credential.token);
      if (!authorization) return inactive("repository_access_revoked");
    }
    console.info(JSON.stringify({ event: "oauth_introspection_allowed", jti: claims.jti, sub: claims.sub, required_scope: requiredScope || null }));
    return Response.json({
      active: true,
      aud: claims.aud,
      client_id: claims.kind === "service" ? claims.sub : "dynamic-public-client",
      exp: claims.exp,
      iat: claims.iat,
      iss: claims.iss,
      scope: claims.scope.join(" "),
      sub: claims.sub,
      token_type: "Bearer",
    }, { headers: { "cache-control": "no-store" } });
  } catch {
    reason = "authorization_unavailable";
    return inactive(reason, 503);
  }
}

function bearer(value: string | null): string | null {
  const match = /^Bearer ([A-Za-z0-9._-]{20,8192})$/.exec(value ?? "");
  return match?.[1] ?? null;
}

function inactive(reason: string, status = 200): Response {
  console.info(JSON.stringify({ event: "oauth_introspection_denied", reason }));
  return Response.json({ active: false }, { status, headers: { "cache-control": "no-store" } });
}
