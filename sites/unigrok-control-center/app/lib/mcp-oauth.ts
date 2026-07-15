import {
  randomBase64Url,
  sha256Base64Url,
  signCookiePayload,
  verifyCookiePayload,
} from "./signed-cookie";

export const MCP_OAUTH_SCOPES = Object.freeze([
  "unigrok:connect",
  "unigrok:invoke",
  "unigrok:review",
  "unigrok:chat",
  "unigrok:status",
]);

const CODE_TTL_SECONDS = 90;
const TOKEN_TTL_SECONDS = 10 * 60;
const CLIENT_PREFIX = "ugclient.";
const CODE_PREFIX = "ugcode.";
const TOKEN_PREFIX = "ugtoken.";

export type McpOAuthConfig = {
  issuer: string;
  resource: string;
  secret: string;
};

export type McpAccessClaims = {
  aud: string;
  exp: number;
  githubId?: number;
  githubLogin?: string;
  iat: number;
  iss: string;
  jti: string;
  kind: "service" | "user";
  scope: string[];
  sub: string;
  v: 1;
};

type ClientRegistration = {
  clientName: string;
  redirectUris: string[];
  v: 1;
};

type AuthorizationCode = {
  challenge: string;
  clientId: string;
  exp: number;
  githubId: number;
  githubLogin: string;
  iat: number;
  jti: string;
  redirectUri: string;
  scope: string[];
  v: 1;
};

export class McpOAuthError extends Error {
  readonly oauthCode: string;

  constructor(oauthCode = "invalid_request") {
    super("OAuth request could not be completed.");
    this.name = "McpOAuthError";
    this.oauthCode = oauthCode;
  }
}

export function loadMcpOAuthConfig(environment: NodeJS.ProcessEnv = process.env): McpOAuthConfig {
  const issuer = normalizeHttpsOrigin(environment.APP_BASE_URL);
  const resource = normalizeHttpsResource(environment.MCP_RESOURCE_URL);
  const secret = environment.MCP_TOKEN_SECRET?.trim() ?? "";
  if (!issuer || !resource || secret.length < 32 || secret.length > 4_096) {
    throw new McpOAuthError("temporarily_unavailable");
  }
  return { issuer, resource, secret };
}

export async function registerOAuthClient(
  config: McpOAuthConfig,
  input: unknown,
): Promise<{ client_id: string; client_id_issued_at: number; client_name: string; redirect_uris: string[] }> {
  const record = readRecord(input);
  const redirectUris = readRedirectUris(record?.redirect_uris);
  const clientName = safeClientName(record?.client_name);
  if (!redirectUris || !clientName) throw new McpOAuthError("invalid_client_metadata");
  const registration: ClientRegistration = { clientName, redirectUris, v: 1 };
  const clientId = `${CLIENT_PREFIX}${await signCookiePayload(registration, config.secret)}`;
  return {
    client_id: clientId,
    client_id_issued_at: Math.floor(Date.now() / 1_000),
    client_name: clientName,
    redirect_uris: redirectUris,
  };
}

export async function validateOAuthClient(
  config: McpOAuthConfig,
  clientId: string,
  redirectUri: string,
): Promise<ClientRegistration> {
  if (!clientId.startsWith(CLIENT_PREFIX) || clientId.length > 8_192) {
    throw new McpOAuthError("unauthorized_client");
  }
  const payload = await verifyCookiePayload(clientId.slice(CLIENT_PREFIX.length), config.secret);
  if (!isClientRegistration(payload) || !payload.redirectUris.includes(redirectUri)) {
    throw new McpOAuthError("unauthorized_client");
  }
  return payload;
}

export function normalizeScopes(value: string | null | undefined): string[] {
  const requested = (value ?? "").split(/\s+/u).filter(Boolean);
  const scopes = requested.length ? requested : ["unigrok:connect", "unigrok:invoke"];
  if (scopes.length > MCP_OAUTH_SCOPES.length || scopes.some((scope) => !MCP_OAUTH_SCOPES.includes(scope))) {
    throw new McpOAuthError("invalid_scope");
  }
  return [...new Set(scopes)];
}

export async function createAuthorizationCode(
  config: McpOAuthConfig,
  input: {
    challenge: string;
    clientId: string;
    githubId: number;
    githubLogin: string;
    redirectUri: string;
    scope: string[];
  },
  now = Date.now(),
): Promise<string> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(input.challenge)) throw new McpOAuthError("invalid_request");
  await validateOAuthClient(config, input.clientId, input.redirectUri);
  const iat = Math.floor(now / 1_000);
  const code: AuthorizationCode = {
    challenge: input.challenge,
    clientId: input.clientId,
    exp: iat + CODE_TTL_SECONDS,
    githubId: input.githubId,
    githubLogin: input.githubLogin,
    iat,
    jti: randomBase64Url(24),
    redirectUri: input.redirectUri,
    scope: input.scope,
    v: 1,
  };
  return `${CODE_PREFIX}${await signCookiePayload(code, config.secret)}`;
}

export async function exchangeAuthorizationCode(
  config: McpOAuthConfig,
  input: { clientId: string; code: string; redirectUri: string; verifier: string },
  now = Date.now(),
): Promise<{ access_token: string; expires_in: number; scope: string; token_type: "Bearer" }> {
  if (!input.code.startsWith(CODE_PREFIX) || !/^[A-Za-z0-9._-]{43,8192}$/.test(input.code)) {
    throw new McpOAuthError("invalid_grant");
  }
  if (!/^[A-Za-z0-9._~-]{43,128}$/.test(input.verifier)) throw new McpOAuthError("invalid_grant");
  const payload = await verifyCookiePayload(input.code.slice(CODE_PREFIX.length), config.secret);
  const nowSeconds = Math.floor(now / 1_000);
  if (
    !isAuthorizationCode(payload) ||
    payload.exp <= nowSeconds ||
    payload.clientId !== input.clientId ||
    payload.redirectUri !== input.redirectUri ||
    (await sha256Base64Url(input.verifier)) !== payload.challenge
  ) {
    throw new McpOAuthError("invalid_grant");
  }
  await validateOAuthClient(config, input.clientId, input.redirectUri);
  const claims: McpAccessClaims = {
    aud: config.resource,
    exp: payload.iat + TOKEN_TTL_SECONDS,
    githubId: payload.githubId,
    githubLogin: payload.githubLogin,
    iat: payload.iat,
    iss: config.issuer,
    jti: payload.jti,
    kind: "user",
    scope: payload.scope,
    sub: `github:${payload.githubId}`,
    v: 1,
  };
  // The code deterministically produces one token. A replay inside the short
  // code window cannot mint additional identities, audiences, scopes, or JTIs.
  const accessToken = `${TOKEN_PREFIX}${await signCookiePayload(claims, config.secret)}`;
  return {
    access_token: accessToken,
    expires_in: Math.max(1, claims.exp - nowSeconds),
    scope: claims.scope.join(" "),
    token_type: "Bearer",
  };
}

/** Allowed headless services and the capability scopes they may hold. */
export const MCP_SERVICE_SPECS = Object.freeze({
  "github-review-broker": {
    scopes: Object.freeze(["unigrok:review"] as const),
    ttlSeconds: 120,
  },
  "cursor-cloud": {
    // Status is granted only inside the fixed cursor-cloud bundle below.
    scopes: Object.freeze(["unigrok:invoke"] as const),
    ttlSeconds: TOKEN_TTL_SECONDS,
  },
} as const);

export type McpServiceName = keyof typeof MCP_SERVICE_SPECS;

export async function createServiceAccessToken(
  config: McpOAuthConfig,
  service: McpServiceName,
  scope: "unigrok:review" | "unigrok:invoke" | "unigrok:status",
  now = Date.now(),
): Promise<string> {
  const spec = MCP_SERVICE_SPECS[service];
  if (!spec || !(spec.scopes as readonly string[]).includes(scope)) {
    throw new McpOAuthError("invalid_scope");
  }
  const iat = Math.floor(now / 1_000);
  // cursor-cloud always gets invoke+status so discover/status + agent work.
  const granted =
    service === "cursor-cloud"
      ? ["unigrok:connect", "unigrok:invoke", "unigrok:status"]
      : ["unigrok:connect", scope];
  const claims: McpAccessClaims = {
    aud: config.resource,
    exp: iat + spec.ttlSeconds,
    iat,
    iss: config.issuer,
    jti: randomBase64Url(24),
    kind: "service",
    scope: granted,
    sub: `service:${service}`,
    v: 1,
  };
  return `${TOKEN_PREFIX}${await signCookiePayload(claims, config.secret)}`;
}

export async function readAccessToken(
  config: McpOAuthConfig,
  token: string,
  now = Date.now(),
): Promise<McpAccessClaims | null> {
  if (!token.startsWith(TOKEN_PREFIX) || token.length > 8_192) return null;
  const payload = await verifyCookiePayload(token.slice(TOKEN_PREFIX.length), config.secret);
  const nowSeconds = Math.floor(now / 1_000);
  return isAccessClaims(payload, config, nowSeconds) ? payload : null;
}

function isClientRegistration(value: unknown): value is ClientRegistration {
  const record = readRecord(value);
  return record?.v === 1 && safeClientName(record.clientName) === record.clientName && readRedirectUris(record.redirectUris) !== null;
}

function isAuthorizationCode(value: unknown): value is AuthorizationCode {
  const record = readRecord(value);
  return Boolean(
    record?.v === 1 &&
    typeof record.clientId === "string" &&
    typeof record.redirectUri === "string" &&
    typeof record.challenge === "string" &&
    /^[A-Za-z0-9_-]{43}$/.test(record.challenge) &&
    Number.isSafeInteger(record.githubId) &&
    typeof record.githubLogin === "string" &&
    /^[A-Za-z0-9-]{1,39}$/.test(record.githubLogin) &&
    Number.isSafeInteger(record.iat) &&
    Number.isSafeInteger(record.exp) &&
    typeof record.jti === "string" &&
    Array.isArray(record.scope) &&
    record.scope.every((scope) => typeof scope === "string" && MCP_OAUTH_SCOPES.includes(scope)),
  );
}

function isAccessClaims(value: unknown, config: McpOAuthConfig, now: number): value is McpAccessClaims {
  const record = readRecord(value);
  if (
    record?.v !== 1 ||
    record.iss !== config.issuer ||
    record.aud !== config.resource ||
    (record.kind !== "user" && record.kind !== "service") ||
    typeof record.sub !== "string" ||
    typeof record.jti !== "string" ||
    !Number.isSafeInteger(record.iat) ||
    !Number.isSafeInteger(record.exp) ||
    (record.iat as number) > now + 60 ||
    (record.exp as number) <= now ||
    (record.exp as number) - (record.iat as number) > TOKEN_TTL_SECONDS + CODE_TTL_SECONDS ||
    !Array.isArray(record.scope) ||
    record.scope.some((scope) => typeof scope !== "string" || !MCP_OAUTH_SCOPES.includes(scope))
  ) return false;
  if (record.kind === "service") {
    if (record.sub === "service:github-review-broker") {
      return (
        Array.isArray(record.scope) &&
        record.scope.length === 2 &&
        record.scope.includes("unigrok:connect") &&
        record.scope.includes("unigrok:review") &&
        record.scope.every((scope) => scope === "unigrok:connect" || scope === "unigrok:review")
      );
    }
    if (record.sub === "service:cursor-cloud") {
      return (
        Array.isArray(record.scope) &&
        record.scope.length === 3 &&
        record.scope.includes("unigrok:connect") &&
        record.scope.includes("unigrok:invoke") &&
        record.scope.includes("unigrok:status") &&
        record.scope.every(
          (scope) =>
            scope === "unigrok:connect" ||
            scope === "unigrok:invoke" ||
            scope === "unigrok:status",
        )
      );
    }
    return false;
  }
  return Number.isSafeInteger(record.githubId) && record.sub === `github:${record.githubId}` && typeof record.githubLogin === "string";
}

function readRedirectUris(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 8) return null;
  const result: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string" || entry.length > 1_024) return null;
    try {
      const url = new URL(entry);
      const loopback = url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
      if ((url.protocol !== "https:" && !loopback) || url.username || url.password || url.hash) return null;
      result.push(url.toString());
    } catch {
      return null;
    }
  }
  return [...new Set(result)];
}

function safeClientName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized.length >= 1 && normalized.length <= 120 && !/[\u0000-\u001f\u007f]/u.test(normalized) ? normalized : null;
}

function normalizeHttpsOrigin(value: string | undefined): string | null {
  try {
    const url = new URL(value ?? "");
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/") return null;
    return url.origin;
  } catch {
    return null;
  }
}

function normalizeHttpsResource(value: string | undefined): string | null {
  try {
    const url = new URL(value ?? "");
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/mcp") return null;
    return url.toString();
  } catch {
    return null;
  }
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
