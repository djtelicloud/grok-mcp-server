export type IntegrationState = "error" | "loading" | "ready" | "unconfigured";

export type PullRequestSummary = {
  author: string;
  checksPassed: number;
  checksTotal: number;
  number: number;
  releaseImpact: "blocking" | "informational";
  reviewState: "approved" | "changes_requested" | "pending";
  title: string;
  url: string;
};

export function releaseBlockingPullRequests(snapshot: ControlCenterSnapshot): PullRequestSummary[] {
  if (snapshot.pullRequests.state !== "ready") return [];
  return snapshot.pullRequests.items.filter((item) => item.releaseImpact === "blocking");
}

export type GrokReviewFinding = {
  evidencePath: string;
  severity: "high" | "low" | "medium";
  title: string;
};

export type ControlCenterSnapshot = {
  grokReview: {
    findings: GrokReviewFinding[];
    message: string;
    score: number | null;
    state: IntegrationState;
    verdict: string | null;
  };
  pullRequests: {
    items: PullRequestSummary[];
    message: string;
    state: IntegrationState;
  };
};

export function createUnconfiguredSnapshot(repository: string | null): ControlCenterSnapshot {
  const repositoryMessage = repository
    ? `Repository metadata is configured for ${repository}, but no approved PR data adapter is connected.`
    : "Set GITHUB_REPOSITORY and connect an installer-owned PR data adapter.";

  return {
    grokReview: {
      findings: [],
      message: "Connect an approved UniGrok review adapter before displaying review results.",
      score: null,
      state: "unconfigured",
      verdict: null,
    },
    pullRequests: {
      items: [],
      message: repositoryMessage,
      state: "unconfigured",
    },
  };
}
