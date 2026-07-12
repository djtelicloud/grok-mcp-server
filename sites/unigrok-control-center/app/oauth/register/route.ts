import { McpOAuthError, loadMcpOAuthConfig, registerOAuthClient } from "../../lib/mcp-oauth";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    if ((request.headers.get("content-type") ?? "").split(";", 1)[0] !== "application/json") throw new McpOAuthError("invalid_client_metadata");
    const body = await request.json();
    return Response.json(await registerOAuthClient(loadMcpOAuthConfig(), body), {
      status: 201,
      headers: { "cache-control": "no-store", "access-control-allow-origin": "*" },
    });
  } catch (error) {
    const code = error instanceof McpOAuthError ? error.oauthCode : "invalid_client_metadata";
    return Response.json({ error: code }, { status: code === "temporarily_unavailable" ? 503 : 400, headers: { "cache-control": "no-store" } });
  }
}
