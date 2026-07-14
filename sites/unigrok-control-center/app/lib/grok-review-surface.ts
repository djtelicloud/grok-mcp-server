import type { ControlCenterSnapshot, IntegrationState } from "./control-center-contract";
import { loadMcpOAuthConfig } from "./mcp-oauth";

export type GrokReviewCheckEvidence = {
  conclusion: string | null;
  name: string;
  pullNumber: number;
  status: string | null;
};

export type GrokReviewSurfaceInput = {
  oauthConfigured: boolean;
  reviewChecks: GrokReviewCheckEvidence[];
};

export type GrokReviewSurface = ControlCenterSnapshot["grokReview"];

/**
 * Derive Control Center Grok-review UI state from real adapter + check evidence.
 * Never invents scores, findings, or verdicts from missing data.
 */
export function deriveGrokReviewSurface(input: GrokReviewSurfaceInput): GrokReviewSurface {
  const unigrokChecks = input.reviewChecks.filter((check) => isUnigrokReviewCheckName(check.name));
  const completed = unigrokChecks.filter(
    (check) => check.status === "completed" || (check.conclusion !== null && check.conclusion !== ""),
  );
  const success = completed.find((check) => check.conclusion === "success");
  if (success) {
    return surface(
      "ready",
      `Latest UniGrok review check succeeded on PR #${success.pullNumber}. Score and findings appear only when the review broker returns structured content.`,
      "Check passed",
    );
  }
  const failure = completed.find((check) =>
    check.conclusion === "failure" ||
    check.conclusion === "cancelled" ||
    check.conclusion === "timed_out" ||
    check.conclusion === "startup_failure",
  );
  if (failure) {
    return surface(
      "error",
      `Latest UniGrok review check on PR #${failure.pullNumber} concluded ${failure.conclusion}. No synthetic score is shown.`,
      null,
    );
  }
  if (input.oauthConfigured) {
    return surface(
      "ready",
      "Hosted review adapter is configured (MCP OAuth). No completed UniGrok review check on open PRs yet. Trigger @grok review or Control POST /api/control/reviews.",
      "Awaiting review",
    );
  }
  return surface(
    "unconfigured",
    "Hosted UniGrok review is not connected: Control MCP OAuth env is incomplete and no UniGrok review check was found on open PRs. Repository Actions vars may still be set separately.",
    null,
  );
}

export function isUnigrokReviewCheckName(name: string): boolean {
  const normalized = name.trim().toLowerCase();
  return (
    normalized === "unigrok review" ||
    normalized === "unigrok pr review" ||
    normalized.includes("unigrok review")
  );
}

export function mcpOAuthConfigured(
  loadConfig: () => unknown = loadMcpOAuthConfig,
): boolean {
  try {
    loadConfig();
    return true;
  } catch {
    return false;
  }
}

function surface(
  state: IntegrationState,
  message: string,
  verdict: string | null,
): GrokReviewSurface {
  return {
    findings: [],
    message,
    score: null,
    state,
    verdict,
  };
}
