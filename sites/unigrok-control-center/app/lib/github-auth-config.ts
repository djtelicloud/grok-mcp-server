export type GitHubControlMode = "github" | "sites";

export type GitHubAuthConfig = {
  appBaseUrl: URL;
  appId: string;
  clientId: string;
  clientSecret: string;
  installationId: number;
  privateKey: string;
  repository: {
    id: number;
    name: string;
    owner: string;
  };
  sessionSecret: string;
};

export class GitHubAuthConfigurationError extends Error {
  constructor() {
    super("GitHub control authentication is not configured.");
    this.name = "GitHubAuthConfigurationError";
  }
}

export function getGitHubControlMode(
  environment: NodeJS.ProcessEnv = process.env,
): GitHubControlMode {
  const value = readEnvironmentText(environment, "CONTROL_CENTER_MODE").toLowerCase();
  if (!value) return "sites";
  if (value === "github") return "github";
  throw new GitHubAuthConfigurationError();
}

export function loadGitHubAuthConfig(
  environment: NodeJS.ProcessEnv = process.env,
): GitHubAuthConfig {
  const appId = readEnvironmentText(environment, "GITHUB_APP_ID");
  const clientId = readEnvironmentText(environment, "GITHUB_APP_CLIENT_ID");
  const clientSecret = readEnvironmentText(environment, "GITHUB_APP_CLIENT_SECRET");
  const installationIdText = readEnvironmentText(environment, "GITHUB_APP_INSTALLATION_ID");
  const privateKey = normalizePrivateKey(readEnvironmentText(environment, "GITHUB_APP_PRIVATE_KEY"));
  const repositoryName = parseRepository(readEnvironmentText(environment, "GITHUB_REPOSITORY"));
  const repositoryId = parsePositiveIdentifier(
    readEnvironmentText(environment, "GITHUB_REPOSITORY_ID"),
  );
  const repository =
    repositoryName && repositoryId ? { ...repositoryName, id: repositoryId } : null;
  const sessionSecret = readEnvironmentText(environment, "AUTH_SESSION_SECRET");
  const appBaseUrl = parseApplicationOrigin(
    readEnvironmentText(environment, "APP_BASE_URL"),
    environment.NODE_ENV,
  );

  if (
    !/^\d{1,20}$/.test(appId) ||
    !/^[A-Za-z0-9_-]{8,128}$/.test(clientId) ||
    clientSecret.length < 32 ||
    clientSecret.length > 512 ||
    !/^\d{1,20}$/.test(installationIdText) ||
    !privateKeyLooksPlausible(privateKey) ||
    !repository ||
    sessionSecret.length < 32 ||
    sessionSecret.length > 4_096 ||
    !appBaseUrl
  ) {
    throw new GitHubAuthConfigurationError();
  }

  const installationId = Number(installationIdText);
  if (!Number.isSafeInteger(installationId) || installationId < 1) {
    throw new GitHubAuthConfigurationError();
  }

  return {
    appBaseUrl,
    appId,
    clientId,
    clientSecret,
    installationId,
    privateKey,
    repository,
    sessionSecret,
  };
}

export function getControlCenterOrigin(
  environment: NodeJS.ProcessEnv = process.env,
): URL | null {
  const raw = readEnvironmentText(environment, "CONTROL_CENTER_ORIGIN");
  if (!raw) return null;
  const origin = parseApplicationOrigin(raw, "production");
  if (!origin) throw new GitHubAuthConfigurationError();
  return origin;
}

export function githubCallbackUrl(config: GitHubAuthConfig): URL {
  return new URL("/auth/github/callback", config.appBaseUrl);
}

export function requestHostMatchesApplication(
  config: Pick<GitHubAuthConfig, "appBaseUrl">,
  hostHeader: string | null | undefined,
): boolean {
  if (!hostHeader || hostHeader.length > 255 || /[\s,\/\\@]/.test(hostHeader)) return false;
  return hostHeader.toLowerCase() === config.appBaseUrl.host.toLowerCase();
}

/** True when a browser Origin matches the control app origin exactly.
 *
 * Cookie-authenticated control POSTs must require this: session cookies are
 * SameSite=Lax, which still sends them on same-site sibling-subdomain forms.
 */
export function requestOriginMatchesApplication(
  config: Pick<GitHubAuthConfig, "appBaseUrl">,
  originHeader: string | null | undefined,
): boolean {
  if (!originHeader || originHeader.length > 512) return false;
  try {
    return new URL(originHeader).origin === config.appBaseUrl.origin;
  } catch {
    return false;
  }
}

function readEnvironmentText(
  environment: NodeJS.ProcessEnv,
  key: string,
): string {
  const value = environment[key];
  return typeof value === "string" ? value.trim() : "";
}

function parseRepository(value: string): Omit<GitHubAuthConfig["repository"], "id"> | null {
  const match = /^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9_.-]{1,100})$/.exec(value);
  if (!match || match[2].endsWith(".git")) return null;
  return { owner: match[1], name: match[2] };
}

function parsePositiveIdentifier(value: string): number | null {
  if (!/^\d{1,20}$/.test(value)) return null;
  const identifier = Number(value);
  return Number.isSafeInteger(identifier) && identifier > 0 ? identifier : null;
}

function parseApplicationOrigin(value: string, nodeEnvironment: string | undefined): URL | null {
  if (!value || value.length > 512) return null;
  try {
    const url = new URL(value);
    const localDevelopment =
      nodeEnvironment !== "production" &&
      url.protocol === "http:" &&
      (url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]");
    if (url.protocol !== "https:" && !localDevelopment) return null;
    if (url.username || url.password || url.search || url.hash) return null;
    if (url.pathname !== "/") return null;
    return url;
  } catch {
    return null;
  }
}

function normalizePrivateKey(value: string): string {
  return value.includes("\\n") ? value.replaceAll("\\n", "\n") : value;
}

function privateKeyLooksPlausible(value: string): boolean {
  const supportedLabels = ["PRIVATE KEY", "RSA PRIVATE KEY"];
  return (
    value.length >= 256 &&
    value.length <= 16_384 &&
    supportedLabels.some((label) => {
      const begin = ["-----BEGIN ", label, "-----"].join("");
      const end = ["-----END ", label, "-----"].join("");
      return value.startsWith(`${begin}\n`) && value.endsWith(end);
    })
  );
}
