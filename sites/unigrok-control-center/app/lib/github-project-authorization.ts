export type GitHubProjectRole = "admin" | "contributor";

export type GitHubProjectAuthorization =
  | {
      authorized: true;
      githubLogin: string;
      role: GitHubProjectRole;
      source: "live-github-collaborator" | "server-configured-bootstrap-binding";
    }
  | {
      authorized: false;
      reason: "invalid-configuration" | "not-authorized" | "not-configured";
    };

type IdentityBinding = {
  chatgpt_email: string;
  github_login: string;
  role: GitHubProjectRole;
};

const BINDINGS_KEY = "UNIGROK_GITHUB_IDENTITY_BINDINGS";
const MAX_BINDINGS_BYTES = 16_384;
const MAX_BINDINGS = 100;

/**
 * Independent project authorization for the contributor control surface.
 *
 * SIWC only authenticates the ChatGPT viewer. This adapter separately maps
 * that viewer to a GitHub login and project role using server-held deployment
 * configuration. Missing, malformed, duplicate, or unmatched configuration
 * always denies access. It is intentionally not presented as live GitHub OAuth
 * or a live collaborator lookup.
 */
export function getGitHubProjectAuthorization(
  chatGPTEmail: string,
): GitHubProjectAuthorization {
  const raw = process.env[BINDINGS_KEY]?.trim() ?? "";
  if (!raw) return { authorized: false, reason: "not-configured" };
  if (new TextEncoder().encode(raw).byteLength > MAX_BINDINGS_BYTES) {
    return { authorized: false, reason: "invalid-configuration" };
  }

  let bindings: unknown;
  try {
    bindings = JSON.parse(raw);
  } catch {
    return { authorized: false, reason: "invalid-configuration" };
  }

  if (!Array.isArray(bindings) || bindings.length > MAX_BINDINGS) {
    return { authorized: false, reason: "invalid-configuration" };
  }

  const validated: IdentityBinding[] = [];
  const emails = new Set<string>();
  for (const candidate of bindings) {
    const binding = validateBinding(candidate);
    if (!binding) return { authorized: false, reason: "invalid-configuration" };
    const email = normalizeEmail(binding.chatgpt_email);
    if (emails.has(email)) {
      return { authorized: false, reason: "invalid-configuration" };
    }
    emails.add(email);
    validated.push({ ...binding, chatgpt_email: email });
  }

  const match = validated.find(
    (binding) => binding.chatgpt_email === normalizeEmail(chatGPTEmail),
  );
  if (!match) return { authorized: false, reason: "not-authorized" };

  return {
    authorized: true,
    githubLogin: match.github_login,
    role: match.role,
    source: "server-configured-bootstrap-binding",
  };
}

function validateBinding(value: unknown): IdentityBinding | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (
    Object.keys(candidate).sort().join(",") !==
    "chatgpt_email,github_login,role"
  ) {
    return null;
  }
  if (
    typeof candidate.chatgpt_email !== "string" ||
    typeof candidate.github_login !== "string" ||
    (candidate.role !== "admin" && candidate.role !== "contributor")
  ) {
    return null;
  }

  const email = normalizeEmail(candidate.chatgpt_email);
  const githubLogin = candidate.github_login.trim();
  if (!isValidEmail(email) || !isValidGitHubLogin(githubLogin)) return null;

  return {
    chatgpt_email: email,
    github_login: githubLogin,
    role: candidate.role,
  };
}

function normalizeEmail(value: string): string {
  return value.normalize("NFKC").trim().toLowerCase();
}

function isValidEmail(value: string): boolean {
  return (
    value.length <= 254 &&
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) &&
    !/[\u0000-\u001F\u007F-\u009F]/.test(value)
  );
}

function isValidGitHubLogin(value: string): boolean {
  return (
    value.length >= 1 &&
    value.length <= 39 &&
    /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(value)
  );
}
