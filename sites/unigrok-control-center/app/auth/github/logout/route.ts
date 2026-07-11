import {
  GitHubAuthConfigurationError,
  getGitHubControlMode,
  loadGitHubAuthConfig,
  requestHostMatchesApplication,
} from "../../../lib/github-auth-config";
import { clearGitHubSessionCookie } from "../../../lib/github-oauth";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    if (getGitHubControlMode() !== "github") return new Response(null, { status: 404 });
    const config = loadGitHubAuthConfig();
    if (
      !requestHostMatchesApplication(config, request.headers.get("host")) ||
      request.headers.get("origin") !== config.appBaseUrl.origin
    ) {
      return new Response("Sign-out is unavailable.", { status: 400 });
    }
    const headers = new Headers({
      "cache-control": "no-store, max-age=0",
      location: config.appBaseUrl.toString(),
      "x-content-type-options": "nosniff",
    });
    headers.append("set-cookie", clearGitHubSessionCookie(config));
    return new Response(null, { headers, status: 303 });
  } catch (error) {
    return new Response("Sign-out is unavailable.", {
      headers: { "cache-control": "no-store", "content-type": "text/plain; charset=utf-8" },
      status: error instanceof GitHubAuthConfigurationError ? 503 : 503,
    });
  }
}
