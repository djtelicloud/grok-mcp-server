import type { GitHubAuthConfig } from "./github-auth-config";
import { githubCallbackUrl } from "./github-auth-config";
import {
  randomBase64Url,
  readCookie,
  serializeCookie,
  sha256Base64Url,
  signCookiePayload,
  verifyCookiePayload,
} from "./signed-cookie";

const OAUTH_TRANSACTION_TTL_SECONDS = 10 * 60;
const SESSION_TTL_SECONDS = 60 * 60;
const OAUTH_REQUEST_TIMEOUT_MS = 8_000;
const OAUTH_MAX_RESPONSE_BYTES = 65_536;
const GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize";
const GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token";
const GITHUB_USER_URL = "https://api.github.com/user";

export type GitHubIdentity = {
  id: number;
  login: string;
};

export type GitHubSession = GitHubIdentity & {
  expiresAt: number;
  issuedAt: number;
  version: 1;
};

export type OAuthTransaction = {
  expiresAt: number;
  issuedAt: number;
  returnTo: string;
  state: string;
  verifier: string;
  version: 1;
};

export class GitHubOAuthError extends Error {
  constructor() {
    super("GitHub authentication could not be completed.");
    this.name = "GitHubOAuthError";
  }
}

export async function createOAuthTransaction(
  returnTo: string,
  now = Date.now(),
  random: (byteLength?: number) => string = randomBase64Url,
): Promise<{ authorizationState: string; codeChallenge: string; transaction: OAuthTransaction }> {
  const issuedAt = Math.floor(now / 1_000);
  const state = random(32);
  const verifier = random(48);
  if (!isOAuthRandomValue(state, 43, 172) || !isOAuthRandomValue(verifier, 43, 128)) {
    throw new GitHubOAuthError();
  }
  return {
    authorizationState: state,
    codeChallenge: await sha256Base64Url(verifier),
    transaction: {
      expiresAt: issuedAt + OAUTH_TRANSACTION_TTL_SECONDS,
      issuedAt,
      returnTo: safeReturnPath(returnTo),
      state,
      verifier,
      version: 1,
    },
  };
}

export function buildGitHubAuthorizationUrl(
  config: GitHubAuthConfig,
  authorizationState: string,
  codeChallenge: string,
): URL {
  if (!isOAuthRandomValue(authorizationState, 43, 172) || !isOAuthRandomValue(codeChallenge, 43, 43)) {
    throw new GitHubOAuthError();
  }
  const url = new URL(GITHUB_AUTHORIZE_URL);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", githubCallbackUrl(config).toString());
  url.searchParams.set("state", authorizationState);
  url.searchParams.set("code_challenge", codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("allow_signup", "false");
  return url;
}

export async function createOAuthStateCookie(
  config: GitHubAuthConfig,
  transaction: OAuthTransaction,
): Promise<string> {
  const value = await signCookiePayload(transaction, config.sessionSecret);
  return serializeCookie(oauthStateCookieName(config), value, {
    maxAge: OAUTH_TRANSACTION_TTL_SECONDS,
    secure: config.appBaseUrl.protocol === "https:",
  });
}

export async function readOAuthTransaction(
  config: GitHubAuthConfig,
  cookieHeader: string | null,
  returnedState: string,
  now = Date.now(),
): Promise<OAuthTransaction | null> {
  if (!isOAuthRandomValue(returnedState, 43, 172)) return null;
  const cookie = readCookie(cookieHeader, oauthStateCookieName(config));
  const value = await verifyCookiePayload(cookie, config.sessionSecret);
  if (!isOAuthTransaction(value, Math.floor(now / 1_000))) return null;
  return value.state === returnedState ? value : null;
}

export function clearOAuthStateCookie(config: GitHubAuthConfig): string {
  return serializeCookie(oauthStateCookieName(config), "", {
    expires: new Date(0),
    maxAge: 0,
    secure: config.appBaseUrl.protocol === "https:",
  });
}

export async function createGitHubSessionCookie(
  config: GitHubAuthConfig,
  identity: GitHubIdentity,
  now = Date.now(),
): Promise<string> {
  if (!isGitHubIdentity(identity)) throw new GitHubOAuthError();
  const issuedAt = Math.floor(now / 1_000);
  const session: GitHubSession = {
    expiresAt: issuedAt + SESSION_TTL_SECONDS,
    id: identity.id,
    issuedAt,
    login: identity.login,
    version: 1,
  };
  const value = await signCookiePayload(session, config.sessionSecret);
  return serializeCookie(sessionCookieName(config), value, {
    maxAge: SESSION_TTL_SECONDS,
    secure: config.appBaseUrl.protocol === "https:",
  });
}

export async function readGitHubSession(
  config: GitHubAuthConfig,
  cookieHeader: string | null,
  now = Date.now(),
): Promise<GitHubSession | null> {
  const cookie = readCookie(cookieHeader, sessionCookieName(config));
  const value = await verifyCookiePayload(cookie, config.sessionSecret);
  return isGitHubSession(value, Math.floor(now / 1_000)) ? value : null;
}

export function clearGitHubSessionCookie(config: GitHubAuthConfig): string {
  return serializeCookie(sessionCookieName(config), "", {
    expires: new Date(0),
    maxAge: 0,
    secure: config.appBaseUrl.protocol === "https:",
  });
}

export async function exchangeCodeForGitHubIdentity(
  config: GitHubAuthConfig,
  code: string,
  verifier: string,
  request: typeof fetch = fetch,
): Promise<GitHubIdentity> {
  if (!/^[A-Za-z0-9_-]{8,512}$/.test(code) || !isOAuthRandomValue(verifier, 43, 128)) {
    throw new GitHubOAuthError();
  }

  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    code_verifier: verifier,
    redirect_uri: githubCallbackUrl(config).toString(),
  });
  const tokenDocument = await requestOAuthJson(GITHUB_TOKEN_URL, {
    body,
    cache: "no-store",
    headers: { accept: "application/json", "content-type": "application/x-www-form-urlencoded" },
    method: "POST",
    redirect: "error",
  }, request);
  const accessToken = readAccessToken(tokenDocument);
  if (!accessToken) throw new GitHubOAuthError();

  const identity = await requestOAuthJson(GITHUB_USER_URL, {
    cache: "no-store",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${accessToken}`,
      "x-github-api-version": "2022-11-28",
    },
    redirect: "error",
  }, request);
  if (!isGitHubIdentity(identity)) throw new GitHubOAuthError();
  return { id: identity.id, login: identity.login };
}

export function safeReturnPath(value: string | null | undefined): string {
  if (!value || value.length > 1_024 || !value.startsWith("/") || value.startsWith("//")) {
    return "/control";
  }
  try {
    const url = new URL(value, "https://control.local");
    if (url.origin !== "https://control.local" || url.pathname.startsWith("/auth/github/")) {
      return "/control";
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/control";
  }
}

function oauthStateCookieName(config: GitHubAuthConfig): string {
  return config.appBaseUrl.protocol === "https:"
    ? "__Host-unigrok-github-state"
    : "unigrok-github-state";
}

function sessionCookieName(config: GitHubAuthConfig): string {
  return config.appBaseUrl.protocol === "https:"
    ? "__Host-unigrok-github-session"
    : "unigrok-github-session";
}

function isOAuthTransaction(value: unknown, now: number): value is OAuthTransaction {
  if (!hasExactKeys(value, ["expiresAt", "issuedAt", "returnTo", "state", "verifier", "version"])) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    candidate.version === 1 &&
    Number.isSafeInteger(candidate.issuedAt) &&
    Number.isSafeInteger(candidate.expiresAt) &&
    (candidate.issuedAt as number) <= now + 60 &&
    (candidate.expiresAt as number) > now &&
    (candidate.expiresAt as number) - (candidate.issuedAt as number) === OAUTH_TRANSACTION_TTL_SECONDS &&
    typeof candidate.returnTo === "string" &&
    safeReturnPath(candidate.returnTo) === candidate.returnTo &&
    typeof candidate.state === "string" &&
    isOAuthRandomValue(candidate.state, 43, 172) &&
    typeof candidate.verifier === "string" &&
    isOAuthRandomValue(candidate.verifier, 43, 128)
  );
}

function isGitHubSession(value: unknown, now: number): value is GitHubSession {
  if (!hasExactKeys(value, ["expiresAt", "id", "issuedAt", "login", "version"])) return false;
  const candidate = value as Record<string, unknown>;
  const issuedAt = candidate.issuedAt;
  const expiresAt = candidate.expiresAt;
  return (
    candidate.version === 1 &&
    isGitHubIdentity({ id: candidate.id, login: candidate.login }) &&
    Number.isSafeInteger(issuedAt) &&
    Number.isSafeInteger(expiresAt) &&
    (issuedAt as number) <= now + 60 &&
    (expiresAt as number) > now &&
    (expiresAt as number) - (issuedAt as number) === SESSION_TTL_SECONDS
  );
}

function isGitHubIdentity(value: unknown): value is GitHubIdentity {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.id === "number" &&
    Number.isSafeInteger(candidate.id) &&
    candidate.id > 0 &&
    typeof candidate.login === "string" &&
    /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(candidate.login)
  );
}

function hasExactKeys(value: unknown, expected: string[]): value is Record<string, unknown> {
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      Object.keys(value).sort().join(",") === [...expected].sort().join(","),
  );
}

function isOAuthRandomValue(value: string, minimum: number, maximum: number): boolean {
  return value.length >= minimum && value.length <= maximum && /^[A-Za-z0-9_-]+$/.test(value);
}

function readAccessToken(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const token = (value as Record<string, unknown>).access_token;
  return typeof token === "string" && token.length >= 20 && token.length <= 512 ? token : null;
}

async function requestOAuthJson(
  url: string,
  init: RequestInit,
  request: typeof fetch,
): Promise<unknown> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), OAUTH_REQUEST_TIMEOUT_MS);
  try {
    const response = await request(url, { ...init, signal: controller.signal });
    if (!response.ok) throw new GitHubOAuthError();
    return await readBoundedJson(response);
  } catch {
    throw new GitHubOAuthError();
  } finally {
    clearTimeout(timeout);
  }
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const contentLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > OAUTH_MAX_RESPONSE_BYTES) {
    throw new GitHubOAuthError();
  }
  if (!response.body) throw new GitHubOAuthError();
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let bytesRead = 0;
  let text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytesRead += value.byteLength;
      if (bytesRead > OAUTH_MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new GitHubOAuthError();
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text) as unknown;
  } catch {
    throw new GitHubOAuthError();
  } finally {
    reader.releaseLock();
  }
}
