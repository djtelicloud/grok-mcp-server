import { createAppAuth } from "@octokit/auth-app";
import type { GitHubAuthConfig } from "./github-auth-config";
import type { GitHubIdentity } from "./github-oauth";
import type { GitHubProjectRole } from "./github-project-authorization";

const GITHUB_API_ORIGIN = "https://api.github.com";
const MAX_GITHUB_RESPONSE_BYTES = 512_000;
const GITHUB_REQUEST_TIMEOUT_MS = 8_000;

export const INSTALLATION_TOKEN_SCOPE = Object.freeze({
  permissions: Object.freeze({
    actions: "read",
    administration: "read",
    checks: "read",
    deployments: "read",
    metadata: "read",
    pull_requests: "read",
    statuses: "read",
  }),
});

export type LiveGitHubAuthorization = {
  authorized: true;
  githubLogin: string;
  role: GitHubProjectRole;
  source: "live-github-collaborator";
};

export type GitHubInstallationCredential = {
  expiresAt: string;
  token: string;
};

export class GitHubApiError extends Error {
  readonly status: number | null;

  constructor(status: number | null = null) {
    super("GitHub project data is temporarily unavailable.");
    this.name = "GitHubApiError";
    this.status = status;
  }
}

export async function createInstallationCredential(
  config: GitHubAuthConfig,
): Promise<GitHubInstallationCredential> {
  try {
    const authenticate = createAppAuth({
      appId: config.appId,
      installationId: config.installationId,
      privateKey: config.privateKey,
    });
    const result = await authenticate({
      permissions: INSTALLATION_TOKEN_SCOPE.permissions,
      repositoryIds: [config.repository.id],
      type: "installation",
    });
    if (
      result.type !== "token" ||
      typeof result.token !== "string" ||
      result.token.length < 20 ||
      typeof result.expiresAt !== "string"
    ) {
      throw new GitHubApiError();
    }
    return { expiresAt: result.expiresAt, token: result.token };
  } catch (error) {
    if (error instanceof GitHubApiError) throw error;
    throw new GitHubApiError();
  }
}

export async function authorizeGitHubCollaborator(
  config: GitHubAuthConfig,
  identity: GitHubIdentity,
  installationToken: string,
  request: typeof fetch = fetch,
): Promise<LiveGitHubAuthorization | null> {
  const path = `/repos/${encodeURIComponent(config.repository.owner)}/${encodeURIComponent(config.repository.name)}/collaborators/${encodeURIComponent(identity.login)}/permission`;
  const response = await githubRequest(path, installationToken, request, { allowNotFound: true });
  if (response === null) return null;
  if (!response || typeof response !== "object" || Array.isArray(response)) {
    throw new GitHubApiError();
  }

  const candidate = response as Record<string, unknown>;
  const user = candidate.user;
  const permission = normalizePermission(candidate.permission, candidate.role_name);
  if (
    !user ||
    typeof user !== "object" ||
    Array.isArray(user) ||
    (user as Record<string, unknown>).id !== identity.id ||
    typeof (user as Record<string, unknown>).login !== "string" ||
    ((user as Record<string, unknown>).login as string).toLowerCase() !== identity.login.toLowerCase() ||
    !permission
  ) {
    return null;
  }

  return {
    authorized: true,
    githubLogin: (user as Record<string, unknown>).login as string,
    role: permission === "admin" ? "admin" : "contributor",
    source: "live-github-collaborator",
  };
}

export async function githubRequest(
  path: string,
  installationToken: string,
  request: typeof fetch = fetch,
  options: {
    accept?: string;
    allowNotFound?: boolean;
    responseType?: "json" | "text";
  } = {},
): Promise<unknown | null> {
  if (!path.startsWith("/") || path.startsWith("//") || path.length > 2_048) {
    throw new GitHubApiError();
  }
  if (installationToken.length < 20 || installationToken.length > 1_024) {
    throw new GitHubApiError();
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), GITHUB_REQUEST_TIMEOUT_MS);
  const response = await request(`${GITHUB_API_ORIGIN}${path}`, {
    cache: "no-store",
    headers: {
      accept: options.accept ?? "application/vnd.github+json",
      authorization: `Bearer ${installationToken}`,
      "x-github-api-version": "2022-11-28",
    },
    method: "GET",
    redirect: "error",
    signal: controller.signal,
  }).catch(() => null).finally(() => clearTimeout(timeout));
  if (!response) throw new GitHubApiError();
  if (options.allowNotFound && response.status === 404) return null;
  if (!response.ok) throw new GitHubApiError(response.status);

  const contentLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > MAX_GITHUB_RESPONSE_BYTES) {
    throw new GitHubApiError();
  }
  const text = await readResponseBody(response);
  if (options.responseType === "text") return text;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new GitHubApiError();
  }
}

async function readResponseBody(response: Response): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let bytesRead = 0;
  let text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytesRead += value.byteLength;
      if (bytesRead > MAX_GITHUB_RESPONSE_BYTES) {
        await reader.cancel();
        throw new GitHubApiError();
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return text;
  } catch (error) {
    if (error instanceof GitHubApiError) throw error;
    throw new GitHubApiError();
  } finally {
    reader.releaseLock();
  }
}

function normalizePermission(
  permissionValue: unknown,
  roleNameValue: unknown,
): "admin" | "maintain" | "triage" | "write" | null {
  const candidates = [roleNameValue, permissionValue];
  for (const value of candidates) {
    if (
      value === "admin" ||
      value === "maintain" ||
      value === "write" ||
      value === "triage"
    ) {
      return value;
    }
  }
  return null;
}
