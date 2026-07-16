import { authorizeGitHubCollaborator, createInstallationCredential } from "../../../lib/github-app";
import { loadGitHubAuthConfig, requestHostMatchesApplication } from "../../../lib/github-auth-config";
import { readGitHubSession } from "../../../lib/github-oauth";
import { callHostedReviewBroker, fetchImmutableReviewEvidence } from "../../../lib/github-review-broker";
import { tryAcquireHostedReviewBudget } from "../../../lib/hosted-review-budget";
import { loadMcpOAuthConfig } from "../../../lib/mcp-oauth";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    const github = loadGitHubAuthConfig();
    if (!requestHostMatchesApplication(github, request.headers.get("host"))) return response(400);
    const session = await readGitHubSession(github, request.headers.get("cookie"));
    if (!session) return response(401);
    const credential = await createInstallationCredential(github);
    const authorization = await authorizeGitHubCollaborator(github, session, credential.token);
    if (!authorization) return response(403);
    const budget = tryAcquireHostedReviewBudget(session.login);
    if (!budget.ok) {
      return Response.json(
        { error: "rate_limited" },
        {
          status: 429,
          headers: {
            "cache-control": "no-store",
            "retry-after": String(budget.retryAfterSec),
          },
        },
      );
    }
    try {
      const body = await request.json() as Record<string, unknown>;
      const pullNumber = body.pull_number;
      const expectedHeadSha = body.expected_head_sha;
      if (!Number.isSafeInteger(pullNumber) || typeof expectedHeadSha !== "string") return response(400);
      const evidence = await fetchImmutableReviewEvidence(github, credential.token, pullNumber as number, expectedHeadSha);
      const review = await callHostedReviewBroker(loadMcpOAuthConfig(), evidence);
      console.info(JSON.stringify({ event: "hosted_review_completed", actor: session.login, pull_number: pullNumber, head_sha: evidence.headSha, base_sha: evidence.baseSha }));
      return Response.json({ schema_version: "unigrok-hosted-review-v1", repository: evidence.repository, pull_number: pullNumber, head_sha: evidence.headSha, base_sha: evidence.baseSha, ...review }, { headers: { "cache-control": "no-store" } });
    } finally {
      budget.release();
    }
  } catch {
    return response(503);
  }
}

function response(status: number): Response {
  return Response.json({ error: status === 401 ? "authentication_required" : status === 403 ? "forbidden" : status === 400 ? "invalid_request" : "review_unavailable" }, { status, headers: { "cache-control": "no-store" } });
}
