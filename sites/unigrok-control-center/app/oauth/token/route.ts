import { McpOAuthError, exchangeAuthorizationCode, loadMcpOAuthConfig } from "../../lib/mcp-oauth";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    const contentType = (request.headers.get("content-type") ?? "").split(";", 1)[0];
    if (contentType !== "application/x-www-form-urlencoded") throw new McpOAuthError("invalid_request");
    const form = new URLSearchParams(await request.text());
    if (form.get("grant_type") !== "authorization_code") throw new McpOAuthError("unsupported_grant_type");
    const result = await exchangeAuthorizationCode(loadMcpOAuthConfig(), {
      clientId: form.get("client_id") ?? "",
      code: form.get("code") ?? "",
      redirectUri: form.get("redirect_uri") ?? "",
      verifier: form.get("code_verifier") ?? "",
    });
    return Response.json(result, { headers: { "cache-control": "no-store", pragma: "no-cache" } });
  } catch (error) {
    const code = error instanceof McpOAuthError ? error.oauthCode : "invalid_request";
    return Response.json({ error: code }, { status: code === "temporarily_unavailable" ? 503 : 400, headers: { "cache-control": "no-store" } });
  }
}
