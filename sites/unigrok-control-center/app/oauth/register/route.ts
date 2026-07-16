import { McpOAuthError, loadMcpOAuthConfig, registerOAuthClient } from "../../lib/mcp-oauth";
import {
  registrationClientKey,
  tryAcquireOAuthRegisterBudget,
} from "../../lib/oauth-register-budget";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    const budget = tryAcquireOAuthRegisterBudget(registrationClientKey(request));
    if (!budget.ok) {
      return Response.json(
        { error: "temporarily_unavailable" },
        {
          status: 429,
          headers: {
            "cache-control": "no-store",
            "retry-after": String(budget.retryAfterSec),
          },
        },
      );
    }
    if ((request.headers.get("content-type") ?? "").split(";", 1)[0] !== "application/json") {
      throw new McpOAuthError("invalid_client_metadata");
    }
    const body = await request.json();
    return Response.json(await registerOAuthClient(loadMcpOAuthConfig(), body), {
      status: 201,
      headers: { "cache-control": "no-store", "access-control-allow-origin": "*" },
    });
  } catch (error) {
    const code = error instanceof McpOAuthError ? error.oauthCode : "invalid_client_metadata";
    return Response.json({ error: code }, {
      status: code === "temporarily_unavailable" ? 503 : 400,
      headers: { "cache-control": "no-store" },
    });
  }
}
