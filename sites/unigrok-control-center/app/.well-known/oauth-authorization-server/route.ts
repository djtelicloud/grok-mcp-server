import { MCP_OAUTH_SCOPES, loadMcpOAuthConfig } from "../../lib/mcp-oauth";

export const dynamic = "force-dynamic";

export function GET(): Response {
  try {
    const config = loadMcpOAuthConfig();
    return Response.json({
      issuer: config.issuer,
      authorization_endpoint: `${config.issuer}/oauth/authorize`,
      token_endpoint: `${config.issuer}/oauth/token`,
      registration_endpoint: `${config.issuer}/oauth/register`,
      introspection_endpoint: `${config.issuer}/oauth/introspect`,
      response_types_supported: ["code"],
      grant_types_supported: ["authorization_code"],
      code_challenge_methods_supported: ["S256"],
      token_endpoint_auth_methods_supported: ["none"],
      scopes_supported: MCP_OAUTH_SCOPES,
    }, { headers: { "cache-control": "public, max-age=300", "access-control-allow-origin": "*" } });
  } catch {
    return Response.json({ error: "temporarily_unavailable" }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}
