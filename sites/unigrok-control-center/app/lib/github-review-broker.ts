import type { GitHubAuthConfig } from "./github-auth-config";
import { githubRequest } from "./github-app";
import { createServiceAccessToken, type McpOAuthConfig } from "./mcp-oauth";

const MAX_REVIEW_DIFF_CHARS = 80_000;
const REVIEW_TIMEOUT_MS = 90_000;

export type ReviewEvidence = {
  baseSha: string;
  diff: string;
  headSha: string;
  pullNumber: number;
  repository: string;
  title: string;
};

export async function fetchImmutableReviewEvidence(
  config: GitHubAuthConfig,
  installationToken: string,
  pullNumber: number,
  expectedHeadSha: string,
  request: typeof fetch = fetch,
): Promise<ReviewEvidence> {
  if (!Number.isSafeInteger(pullNumber) || pullNumber < 1 || !isSha(expectedHeadSha)) {
    throw new Error("Invalid review request.");
  }
  const repository = `${config.repository.owner}/${config.repository.name}`;
  const api = `/repos/${config.repository.owner}/${config.repository.name}`;
  const before = readPull(await githubRequest(`${api}/pulls/${pullNumber}`, installationToken, request));
  if (before.headSha !== expectedHeadSha) throw new Error("Pull request head is stale.");
  const diff = await githubRequest(
    `${api}/compare/${before.baseSha}...${before.headSha}`,
    installationToken,
    request,
    { accept: "application/vnd.github.v3.diff", responseType: "text" },
  );
  if (typeof diff !== "string" || !diff.trim() || diff.length > MAX_REVIEW_DIFF_CHARS) {
    throw new Error("Pull request diff is unavailable or exceeds the hosted review limit.");
  }
  const after = readPull(await githubRequest(`${api}/pulls/${pullNumber}`, installationToken, request));
  if (after.headSha !== before.headSha || after.baseSha !== before.baseSha) {
    throw new Error("Pull request changed while review evidence was collected.");
  }
  return { ...after, diff, pullNumber, repository };
}

export async function callHostedReviewBroker(
  oauth: McpOAuthConfig,
  evidence: ReviewEvidence,
  request: typeof fetch = fetch,
): Promise<Record<string, unknown>> {
  const token = await createServiceAccessToken(oauth, "github-review-broker", "unigrok:review");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REVIEW_TIMEOUT_MS);
  try {
    const response = await request(oauth.resource, {
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: `review-${evidence.pullNumber}-${evidence.headSha.slice(0, 12)}`,
        method: "tools/call",
        params: {
          name: "review_pull_request",
          arguments: {
            repository: evidence.repository,
            pull_number: evidence.pullNumber,
            title: evidence.title,
            diff: evidence.diff,
            ci_summary: `Expected immutable head: ${evidence.headSha}; base: ${evidence.baseSha}`,
            review_comments: "Not supplied by the hosted broker.",
            plane: "api",
          },
        },
      }),
      cache: "no-store",
      headers: {
        accept: "application/json, text/event-stream",
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        "mcp-protocol-version": "2025-06-18",
        "x-caller": "github-review-broker",
      },
      method: "POST",
      redirect: "error",
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok || text.length > 1_000_000) throw new Error("Hosted review service is unavailable.");
    return sanitizeReviewResponse(parseMcpResponse(text));
  } finally {
    clearTimeout(timeout);
  }
}

function parseMcpResponse(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed.startsWith("{")) return JSON.parse(trimmed) as unknown;
  const data = trimmed.split("\n").find((line) => line.startsWith("data:"));
  if (!data) throw new Error("Hosted review returned an invalid response.");
  return JSON.parse(data.slice(5).trim()) as unknown;
}

function sanitizeReviewResponse(value: unknown): Record<string, unknown> {
  const document = readRecord(value);
  const result = readRecord(document?.result);
  const structured = readRecord(result?.structuredContent) ?? readRecord(result?.structured_content);
  if (!structured) throw new Error("Hosted review returned no structured result.");
  const review = safeText(structured.review, 100_000);
  if (!review) throw new Error("Hosted review returned no review text.");
  return {
    review,
    model: safeText(structured.model, 120) ?? "unknown",
    plane: safeText(structured.plane, 32) ?? "API",
    route: safeText(structured.route, 64) ?? "unknown",
    cost_usd: finiteNumber(structured.cost_usd),
    degraded: structured.degraded === true,
  };
}

function readPull(value: unknown): Omit<ReviewEvidence, "diff" | "pullNumber" | "repository"> {
  const record = readRecord(value);
  const headSha = readRecord(record?.head)?.sha;
  const baseSha = readRecord(record?.base)?.sha;
  const title = safeText(record?.title, 500);
  if (!isSha(headSha) || !isSha(baseSha) || !title) throw new Error("GitHub returned invalid pull request evidence.");
  return { baseSha, headSha, title };
}

function isSha(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{40}$/.test(value);
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function safeText(value: unknown, max: number): string | null {
  return typeof value === "string" && value.trim() && value.length <= max ? value.trim() : null;
}

function finiteNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}
