import type { GitHubAuthConfig } from "./github-auth-config";
import { GitHubApiError, githubRequest } from "./github-app";
import type {
  ControlCenterSnapshot,
  GitHubDeploymentEvidence,
  GitHubRulesetEvidence,
  GitHubWorkflowRun,
  PullRequestSummary,
} from "./control-center-contract";

type OptionalResult =
  | { ok: true; value: unknown }
  | { ok: false };

type RawPullRequestEvidence = {
  checks: unknown;
  pullRequest: unknown;
  reviews: unknown;
  statuses: unknown;
};

export class GitHubSnapshotError extends Error {
  constructor() {
    super("GitHub project data could not be validated.");
    this.name = "GitHubSnapshotError";
  }
}

export async function fetchGitHubControlSnapshot(
  config: GitHubAuthConfig,
  installationToken: string,
  request: typeof fetch = fetch,
  now = new Date(),
): Promise<ControlCenterSnapshot> {
  const repositoryPath = repositoryApiPath(config);
  const repository = await githubRequest(repositoryPath, installationToken, request);
  assertRepositoryIdentity(repository, config);
  const defaultBranch = readDefaultBranch(repository);

  const [defaultBranchStatus, pullRequestDocument, workflowRuns, deployments, rulesets] = await Promise.all([
    githubRequest(
      `${repositoryPath}/commits/${encodeURIComponent(defaultBranch)}/status`,
      installationToken,
      request,
    ),
    githubRequest(`${repositoryPath}/pulls?state=open&sort=updated&direction=desc&per_page=5`, installationToken, request),
    optionalRequest(`${repositoryPath}/actions/runs?branch=${encodeURIComponent(defaultBranch)}&per_page=1`, installationToken, request),
    optionalRequest(`${repositoryPath}/deployments?per_page=5`, installationToken, request),
    optionalRequest(`${repositoryPath}/rulesets?includes_parents=true&per_page=100`, installationToken, request),
  ]);
  const pullRequests = readArray(pullRequestDocument).slice(0, 5);
  const pullRequestEvidence = await mapWithConcurrency(
    pullRequests,
    2,
    async (pullRequest): Promise<RawPullRequestEvidence> => {
      const number = readPositiveInteger(readRecord(pullRequest)?.number);
      const headSha = readSha(readRecord(readRecord(pullRequest)?.head)?.sha);
      if (!number || !headSha) throw new GitHubSnapshotError();
      const [checks, statuses, reviews] = await Promise.all([
        githubRequest(`${repositoryPath}/commits/${headSha}/check-runs?per_page=100`, installationToken, request),
        githubRequest(`${repositoryPath}/commits/${headSha}/status`, installationToken, request),
        githubRequest(`${repositoryPath}/pulls/${number}/reviews?per_page=100`, installationToken, request),
      ]);
      return { checks, pullRequest, reviews, statuses };
    },
  );

  return sanitizeGitHubControlSnapshot(
    config,
    {
      defaultBranchStatus,
      deployments,
      pullRequests: pullRequestEvidence,
      repository,
      rulesets,
      workflowRuns,
    },
    now,
  );

  async function optionalRequest(path: string, token: string, fetcher: typeof fetch): Promise<OptionalResult> {
    try {
      return { ok: true, value: await githubRequest(path, token, fetcher) };
    } catch (error) {
      if (error instanceof GitHubApiError) return { ok: false };
      throw error;
    }
  }
}

export function sanitizeGitHubControlSnapshot(
  config: Pick<GitHubAuthConfig, "repository">,
  input: unknown,
  now = new Date(),
): ControlCenterSnapshot {
  const raw = readRecord(input);
  if (!raw || !isOptionalResult(raw.workflowRuns) || !isOptionalResult(raw.deployments) || !isOptionalResult(raw.rulesets)) {
    throw new GitHubSnapshotError();
  }

  const repository = readRecord(raw.repository);
  const defaultBranch = readDefaultBranch(repository);
  const headSha = readSha(readRecord(raw.defaultBranchStatus)?.sha);
  if (!headSha || !repository) throw new GitHubSnapshotError();
  const pullRequests = readArray(raw.pullRequests).map((entry) => sanitizePullRequest(entry, config));
  assertRepositoryIdentity(repository, config);
  const workflowRun = raw.workflowRuns.ok
    ? sanitizeLatestWorkflowRun(raw.workflowRuns.value, config)
    : null;
  const deploymentItems = raw.deployments.ok ? sanitizeDeployments(raw.deployments.value) : [];
  const rulesetItems = raw.rulesets.ok ? sanitizeRulesets(raw.rulesets.value) : [];
  const observedAt = validIsoDate(now) ?? new Date(0).toISOString();
  const repositoryUrl = `https://github.com/${config.repository.owner}/${config.repository.name}`;

  return {
    github: {
      ci: raw.workflowRuns.ok
        ? {
            message: workflowRun ? "Latest default-branch workflow run from GitHub." : "No default-branch workflow runs found.",
            run: workflowRun,
            state: "ready",
          }
        : { message: "GitHub Actions data could not be refreshed.", run: null, state: "error" },
      deployments: raw.deployments.ok
        ? {
            items: deploymentItems,
            message: deploymentItems.length ? "Recent GitHub deployment records." : "No GitHub deployment records found.",
            state: "ready",
          }
        : { items: [], message: "GitHub deployment data could not be refreshed.", state: "error" },
      observedAt,
      repository: {
        defaultBranch,
        headSha,
        message: `Fresh installation-token snapshot observed at ${observedAt}.`,
        state: "ready",
        url: repositoryUrl,
      },
      rulesets: raw.rulesets.ok
        ? {
            items: rulesetItems,
            message: rulesetItems.length ? "Active repository governance evidence from GitHub." : "No repository rulesets found.",
            state: "ready",
          }
        : { items: [], message: "GitHub ruleset data could not be refreshed.", state: "error" },
    },
    grokReview: {
      findings: [],
      message: "Hosted UniGrok review is not connected yet.",
      score: null,
      state: "unconfigured",
      verdict: null,
    },
    pullRequests: {
      items: pullRequests,
      message: pullRequests.length
        ? `Showing up to five recently updated open pull requests for ${config.repository.owner}/${config.repository.name}.`
        : "GitHub reports no open pull requests in the first result page.",
      state: "ready",
    },
  };
}

function sanitizePullRequest(
  value: unknown,
  config: Pick<GitHubAuthConfig, "repository">,
): PullRequestSummary {
  const evidence = readRecord(value);
  const pullRequest = readRecord(evidence?.pullRequest);
  const number = readPositiveInteger(pullRequest?.number);
  const title = safeText(pullRequest?.title, 180);
  const author = safeLogin(readRecord(pullRequest?.user)?.login);
  const checksDocument = readRecord(evidence?.checks);
  const checkRuns = readArray(checksDocument?.check_runs).slice(0, 100);
  const statusesDocument = readRecord(evidence?.statuses);
  const statuses = readArray(statusesDocument?.statuses).slice(0, 100);
  if (!number || !title || !author) throw new GitHubSnapshotError();

  const checksPassed =
    checkRuns.filter((run) => readRecord(run)?.conclusion === "success").length +
    statuses.filter((status) => readRecord(status)?.state === "success").length;
  const labels = readArray(pullRequest?.labels)
    .map((label) => safeText(readRecord(label)?.name, 64)?.toLowerCase())
    .filter(Boolean);

  return {
    author,
    checksPassed,
    checksTotal: checkRuns.length + statuses.length,
    number,
    releaseImpact: labels.includes("release-blocker") ? "blocking" : "informational",
    reviewState: sanitizeReviewState(evidence?.reviews),
    title,
    url: `https://github.com/${config.repository.owner}/${config.repository.name}/pull/${number}`,
  };
}

function sanitizeReviewState(value: unknown): PullRequestSummary["reviewState"] {
  const latestByReviewer = new Map<number, string>();
  for (const review of readArray(value).slice(0, 100)) {
    const record = readRecord(review);
    const userId = readPositiveInteger(readRecord(record?.user)?.id);
    const state = typeof record?.state === "string" ? record.state.toUpperCase() : "";
    if (userId && (state === "APPROVED" || state === "CHANGES_REQUESTED" || state === "DISMISSED")) {
      latestByReviewer.set(userId, state);
    }
  }
  const states = [...latestByReviewer.values()];
  if (states.includes("CHANGES_REQUESTED")) return "changes_requested";
  if (states.includes("APPROVED")) return "approved";
  return "pending";
}

function sanitizeLatestWorkflowRun(
  value: unknown,
  config: Pick<GitHubAuthConfig, "repository">,
): GitHubWorkflowRun | null {
  const run = readRecord(readArray(readRecord(value)?.workflow_runs)[0]);
  if (!run) return null;
  const id = readPositiveInteger(run.id);
  const name = safeText(run.name, 120);
  const headSha = readSha(run.head_sha);
  const status = normalizeWorkflowStatus(run.status);
  const conclusion = normalizeWorkflowConclusion(run.conclusion);
  const updatedAt = validIsoDate(run.updated_at);
  if (!id || !name || !headSha || !updatedAt) throw new GitHubSnapshotError();
  const htmlUrl = safeGitHubUrl(
    run.html_url,
    `/${config.repository.owner}/${config.repository.name}/actions/runs/`,
  );
  if (!htmlUrl) throw new GitHubSnapshotError();
  return { conclusion, headSha, name, status, updatedAt, url: htmlUrl };
}

function sanitizeDeployments(value: unknown): GitHubDeploymentEvidence[] {
  return readArray(value).slice(0, 5).map((entry) => {
    const record = readRecord(entry);
    const id = readPositiveInteger(record?.id);
    const environment = safeText(record?.environment, 80);
    const sha = readSha(record?.sha);
    const createdAt = validIsoDate(record?.created_at);
    const rawState = typeof record?.latest_state === "string" ? record.latest_state : "unknown";
    if (!id || !environment || !sha || !createdAt) throw new GitHubSnapshotError();
    return { createdAt, environment, id, sha, state: normalizeDeploymentState(rawState) };
  });
}

function sanitizeRulesets(value: unknown): GitHubRulesetEvidence[] {
  return readArray(value).slice(0, 25).map((entry) => {
    const record = readRecord(entry);
    const id = readPositiveInteger(record?.id);
    const name = safeText(record?.name, 120);
    if (!id || !name) throw new GitHubSnapshotError();
    return {
      enforcement: normalizeRulesetEnforcement(record?.enforcement),
      id,
      name,
      target: normalizeRulesetTarget(record?.target),
    };
  });
}

function repositoryApiPath(config: Pick<GitHubAuthConfig, "repository">): string {
  return `/repos/${encodeURIComponent(config.repository.owner)}/${encodeURIComponent(config.repository.name)}`;
}

function assertRepositoryIdentity(
  value: unknown,
  config: Pick<GitHubAuthConfig, "repository">,
): void {
  const repository = readRecord(value);
  const repositoryId = readPositiveInteger(repository?.id);
  const repositoryOwner = safeLogin(readRecord(repository?.owner)?.login);
  const repositoryName = safeText(repository?.name, 100);
  const fullName = safeText(repository?.full_name, 140);
  const expectedFullName = `${config.repository.owner}/${config.repository.name}`;
  if (
    repositoryId !== config.repository.id ||
    repositoryOwner?.toLowerCase() !== config.repository.owner.toLowerCase() ||
    repositoryName?.toLowerCase() !== config.repository.name.toLowerCase() ||
    fullName?.toLowerCase() !== expectedFullName.toLowerCase()
  ) {
    throw new GitHubSnapshotError();
  }
}

function readDefaultBranch(value: unknown): string {
  const branch = safeText(readRecord(value)?.default_branch, 255);
  if (!branch || !/^[A-Za-z0-9._\/-]+$/.test(branch) || branch.startsWith("/") || branch.includes("..")) {
    throw new GitHubSnapshotError();
  }
  return branch;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function readPositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function readSha(value: unknown): string | null {
  return typeof value === "string" && /^[0-9a-f]{40}$/i.test(value) ? value.toLowerCase() : null;
}

function safeLogin(value: unknown): string | null {
  return typeof value === "string" && /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(value)
    ? value
    : null;
}

function safeText(value: unknown, maximumLength: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value
    .normalize("NFKC")
    .replace(/[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  if (!normalized) return null;
  return [...normalized].slice(0, maximumLength).join("");
}

function safeGitHubUrl(value: unknown, expectedPathPrefix: string): string | null {
  if (typeof value !== "string" || value.length > 1_024) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      url.hostname === "github.com" &&
      !url.username &&
      !url.password &&
      url.pathname.toLowerCase().startsWith(expectedPathPrefix.toLowerCase())
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function validIsoDate(value: unknown): string | null {
  const date = value instanceof Date ? value : typeof value === "string" ? new Date(value) : null;
  return date && Number.isFinite(date.valueOf()) ? date.toISOString() : null;
}

function isOptionalResult(value: unknown): value is OptionalResult {
  const record = readRecord(value);
  if (!record || typeof record.ok !== "boolean") return false;
  return record.ok ? Object.hasOwn(record, "value") : Object.keys(record).length === 1;
}

function normalizeWorkflowStatus(value: unknown): GitHubWorkflowRun["status"] {
  return value === "completed" || value === "in_progress" || value === "queued" || value === "requested" || value === "waiting" || value === "pending"
    ? value
    : "unknown";
}

function normalizeWorkflowConclusion(value: unknown): GitHubWorkflowRun["conclusion"] {
  return value === null || value === "action_required" || value === "cancelled" || value === "failure" || value === "neutral" || value === "skipped" || value === "stale" || value === "success" || value === "timed_out"
    ? value
    : null;
}

function normalizeDeploymentState(value: unknown): GitHubDeploymentEvidence["state"] {
  return value === "error" || value === "failure" || value === "inactive" || value === "in_progress" || value === "pending" || value === "queued" || value === "success"
    ? value
    : "unknown";
}

function normalizeRulesetEnforcement(value: unknown): GitHubRulesetEvidence["enforcement"] {
  return value === "active" || value === "disabled" || value === "evaluate" ? value : "unknown";
}

function normalizeRulesetTarget(value: unknown): GitHubRulesetEvidence["target"] {
  return value === "branch" || value === "push" || value === "repository" || value === "tag" ? value : "unknown";
}

async function mapWithConcurrency<Input, Output>(
  values: Input[],
  concurrency: number,
  mapper: (value: Input) => Promise<Output>,
): Promise<Output[]> {
  const results = new Array<Output>(values.length);
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < values.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await mapper(values[index]);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(concurrency, values.length) }, () => worker()),
  );
  return results;
}
