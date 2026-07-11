import {
  GitHubAuthConfigurationError,
  getGitHubControlMode,
  loadGitHubAuthConfig,
  requestHostMatchesApplication,
} from "../../../lib/github-auth-config";
import {
  GitHubApiError,
  authorizeGitHubCollaborator,
  createInstallationCredential,
} from "../../../lib/github-app";
import {
  GitHubOAuthError,
  clearOAuthStateCookie,
  createGitHubSessionCookie,
  exchangeCodeForGitHubIdentity,
  readOAuthTransaction,
} from "../../../lib/github-oauth";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  let clearStateCookie: string | null = null;
  try {
    if (getGitHubControlMode() !== "github") return genericResponse(404);
    const config = loadGitHubAuthConfig();
    clearStateCookie = clearOAuthStateCookie(config);
    if (!requestHostMatchesApplication(config, request.headers.get("host"))) {
      return genericResponse(400, clearStateCookie);
    }
    const url = new URL(request.url);
    const codes = url.searchParams.getAll("code");
    const returnedStates = url.searchParams.getAll("state");
    if (codes.length !== 1 || returnedStates.length !== 1) throw new GitHubOAuthError();
    const code = codes[0];
    const returnedState = returnedStates[0];
    const transaction = await readOAuthTransaction(
      config,
      request.headers.get("cookie"),
      returnedState,
    );
    if (!transaction) throw new GitHubOAuthError();

    const identity = await exchangeCodeForGitHubIdentity(config, code, transaction.verifier);
    const installationCredential = await createInstallationCredential(config);
    const authorization = await authorizeGitHubCollaborator(
      config,
      identity,
      installationCredential.token,
    );
    if (!authorization) {
      return genericResponse(403, clearStateCookie, "This GitHub account is not an approved project contributor.");
    }

    const headers = redirectHeaders(new URL(transaction.returnTo, config.appBaseUrl).toString());
    headers.append("set-cookie", clearStateCookie);
    headers.append("set-cookie", await createGitHubSessionCookie(config, identity));
    return new Response(null, { headers, status: 302 });
  } catch (error) {
    const status =
      error instanceof GitHubAuthConfigurationError
        ? 503
        : error instanceof GitHubOAuthError
          ? 400
          : error instanceof GitHubApiError
            ? 503
            : 503;
    return genericResponse(status, clearStateCookie);
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

function genericResponse(
  status: number,
  clearStateCookie: string | null = null,
  message = "GitHub authentication could not be completed.",
): Response {
  const headers = new Headers({
    "cache-control": "no-store, max-age=0",
    "content-type": "text/plain; charset=utf-8",
    "x-content-type-options": "nosniff",
  });
  if (clearStateCookie) headers.append("set-cookie", clearStateCookie);
  return new Response(message, { headers, status });
}
