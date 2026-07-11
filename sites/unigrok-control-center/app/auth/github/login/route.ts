import {
  GitHubAuthConfigurationError,
  getGitHubControlMode,
  loadGitHubAuthConfig,
  requestHostMatchesApplication,
} from "../../../lib/github-auth-config";
import {
  GitHubOAuthError,
  buildGitHubAuthorizationUrl,
  createOAuthStateCookie,
  createOAuthTransaction,
} from "../../../lib/github-oauth";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  try {
    if (getGitHubControlMode() !== "github") return genericResponse(404);
    const config = loadGitHubAuthConfig();
    if (!requestHostMatchesApplication(config, request.headers.get("host"))) return genericResponse(400);
    const returnTo = new URL(request.url).searchParams.get("return_to") ?? "/control";
    const { authorizationState, codeChallenge, transaction } = await createOAuthTransaction(returnTo);
    const headers = redirectHeaders(buildGitHubAuthorizationUrl(config, authorizationState, codeChallenge).toString());
    headers.append("set-cookie", await createOAuthStateCookie(config, transaction));
    return new Response(null, { headers, status: 302 });
  } catch (error) {
    return genericResponse(
      error instanceof GitHubAuthConfigurationError ? 503 : error instanceof GitHubOAuthError ? 400 : 503,
    );
  }
}

function redirectHeaders(location: string): Headers {
  return new Headers({
    "cache-control": "no-store, max-age=0",
    location,
    pragma: "no-cache",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
  });
}

function genericResponse(status: number): Response {
  return new Response("GitHub sign-in is unavailable.", {
    headers: {
      "cache-control": "no-store, max-age=0",
      "content-type": "text/plain; charset=utf-8",
      "x-content-type-options": "nosniff",
    },
    status,
  });
}
