import { authorizeGitHubCollaborator, createInstallationCredential } from "../../../lib/github-app";
import { loadGitHubAuthConfig, requestHostMatchesApplication } from "../../../lib/github-auth-config";
import { readGitHubSession } from "../../../lib/github-oauth";
import { buildLandingReceiptPayload, loadReceiptSigningConfig, signLandingReceipt } from "../../../lib/landing-receipt";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    const github = loadGitHubAuthConfig();
    if (!requestHostMatchesApplication(github, request.headers.get("host"))) return failure(400);
    const session = await readGitHubSession(github, request.headers.get("cookie"));
    if (!session) return failure(401);
    const credential = await createInstallationCredential(github);
    const authorization = await authorizeGitHubCollaborator(github, session, credential.token);
    if (!authorization || authorization.role !== "admin") return failure(403);
    const body = await request.json() as Record<string, unknown>;
    if (!Number.isSafeInteger(body.pull_number) || typeof body.expected_head_sha !== "string") return failure(400);
    const payload = await buildLandingReceiptPayload(github, credential.token, { actor: session.login, expectedHeadSha: body.expected_head_sha, pullNumber: body.pull_number as number });
    const receipt = signLandingReceipt(loadReceiptSigningConfig(), payload);
    console.info(JSON.stringify({ event: "landing_receipt_signed", actor: session.login, pull_number: payload.pull_number, head_sha: payload.head_sha, merge_commit_sha: payload.merge_commit_sha, key_id: receipt.key_id }));
    return Response.json(receipt, { headers: { "cache-control": "no-store" } });
  } catch {
    return failure(503);
  }
}

function failure(status: number): Response {
  return Response.json({ error: status === 401 ? "authentication_required" : status === 403 ? "admin_required" : status === 400 ? "invalid_request" : "receipt_unavailable" }, { status, headers: { "cache-control": "no-store" } });
}
