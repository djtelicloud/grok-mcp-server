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

export type GitHubWorkflowRun = {
  conclusion: "action_required" | "cancelled" | "failure" | "neutral" | "skipped" | "stale" | "success" | "timed_out" | null;
  headSha: string;
  name: string;
  status: "completed" | "in_progress" | "queued" | "requested" | "waiting" | "pending" | "unknown";
  updatedAt: string;
  url: string;
};

export type GitHubDeploymentEvidence = {
  createdAt: string;
  environment: string;
  id: number;
  sha: string;
  state: "error" | "failure" | "inactive" | "in_progress" | "pending" | "queued" | "success" | "unknown";
};

export type GitHubRulesetEvidence = {
  enforcement: "active" | "disabled" | "evaluate" | "unknown";
  id: number;
  name: string;
  target: "branch" | "push" | "repository" | "tag" | "unknown";
};

export type GitHubRepositoryEvidence = {
  ci: {
    message: string;
    run: GitHubWorkflowRun | null;
    state: IntegrationState;
  };
  deployments: {
    items: GitHubDeploymentEvidence[];
    message: string;
    state: IntegrationState;
  };
  observedAt: string | null;
  repository: {
    defaultBranch: string | null;
    headSha: string | null;
    message: string;
    state: IntegrationState;
    url: string | null;
  };
  rulesets: {
    items: GitHubRulesetEvidence[];
    message: string;
    state: IntegrationState;
  };
};

export type ControlCenterSnapshot = {
  github: GitHubRepositoryEvidence;
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
    : "Set GITHUB_REPOSITORY and connect an approved server-side PR data adapter.";

  return {
    github: createEmptyGitHubEvidence("unconfigured", repositoryMessage),
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

export function createGitHubErrorSnapshot(repository: string | null): ControlCenterSnapshot {
  const message = repository
    ? `GitHub data for ${repository} could not be refreshed. No cached or synthetic data is shown.`
    : "GitHub project data could not be refreshed.";
  return {
    github: createEmptyGitHubEvidence("error", message),
    grokReview: {
      findings: [],
      message: "Hosted UniGrok review is not connected yet.",
      score: null,
      state: "unconfigured",
      verdict: null,
    },
    pullRequests: { items: [], message, state: "error" },
  };
}

function createEmptyGitHubEvidence(
  state: IntegrationState,
  message: string,
): GitHubRepositoryEvidence {
  return {
    ci: { message, run: null, state },
    deployments: { items: [], message, state },
    observedAt: null,
    repository: {
      defaultBranch: null,
      headSha: null,
      message,
      state,
      url: null,
    },
    rulesets: { items: [], message, state },
  };
}
