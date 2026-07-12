import { createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import type { GitHubAuthConfig } from "./github-auth-config";
import { githubRequest } from "./github-app";

export type ReceiptSigningConfig = {
  keyId: string;
  privateKey: string;
  publicKey: string;
};

export type LandingReceiptPayload = {
  actor: string;
  base_sha: string;
  broker_version: "unigrok-control-center-v1";
  codex_disposition: { check_id: number; name: string };
  head_sha: string;
  issued_at: string;
  merge_commit_sha: string;
  policy_version: "cloud-control-v1";
  pull_number: number;
  repository: string;
  required_checks: Array<{ conclusion: "success"; name: string }>;
  resulting_main_sha: string;
  schema_version: "unigrok-signed-landing-receipt-v1";
};

export type SignedLandingReceipt = {
  alg: "Ed25519";
  key_id: string;
  payload: LandingReceiptPayload;
  signature: string;
};

export function loadReceiptSigningConfig(environment: NodeJS.ProcessEnv = process.env): ReceiptSigningConfig {
  const keyId = environment.RECEIPT_SIGNING_KEY_ID?.trim() ?? "";
  const privateKey = normalizePem(environment.RECEIPT_SIGNING_PRIVATE_KEY ?? "");
  const publicKey = normalizePem(environment.RECEIPT_SIGNING_PUBLIC_KEY ?? "");
  if (!/^[A-Za-z0-9._-]{3,80}$/.test(keyId) || privateKey.length < 100 || publicKey.length < 80) {
    throw new Error("Receipt signing is unavailable.");
  }
  const derivedPublic = createPublicKey(createPrivateKey(privateKey)).export({ format: "pem", type: "spki" }).toString().trim();
  const configuredPublic = createPublicKey(publicKey).export({ format: "pem", type: "spki" }).toString().trim();
  if (derivedPublic !== configuredPublic) throw new Error("Receipt signing keys do not match.");
  return { keyId, privateKey, publicKey };
}

export async function buildLandingReceiptPayload(
  config: GitHubAuthConfig,
  installationToken: string,
  input: { actor: string; expectedHeadSha: string; pullNumber: number },
  request: typeof fetch = fetch,
  now = new Date(),
): Promise<LandingReceiptPayload> {
  if (!Number.isSafeInteger(input.pullNumber) || input.pullNumber < 1 || !isSha(input.expectedHeadSha)) {
    throw new Error("Invalid receipt request.");
  }
  const api = `/repos/${config.repository.owner}/${config.repository.name}`;
  const pull = readRecord(await githubRequest(`${api}/pulls/${input.pullNumber}`, installationToken, request));
  const headSha = readSha(readRecord(pull?.head)?.sha);
  const baseSha = readSha(readRecord(pull?.base)?.sha);
  const mergeCommitSha = readSha(pull?.merge_commit_sha);
  if (!headSha || headSha !== input.expectedHeadSha || !baseSha || !mergeCommitSha || !pull?.merged_at) {
    throw new Error("Pull request is unmerged or its head is stale.");
  }
  const repository = readRecord(await githubRequest(api, installationToken, request));
  const defaultBranch = typeof repository?.default_branch === "string" ? repository.default_branch : "";
  if (!/^[A-Za-z0-9._/-]{1,255}$/.test(defaultBranch)) throw new Error("Default branch is invalid.");
  const main = readRecord(await githubRequest(`${api}/commits/${encodeURIComponent(defaultBranch)}`, installationToken, request));
  const resultingMainSha = readSha(main?.sha);
  if (!resultingMainSha) throw new Error("Default branch head is invalid.");
  const ancestry = readRecord(await githubRequest(`${api}/compare/${mergeCommitSha}...${resultingMainSha}`, installationToken, request));
  if (!ancestry || !["identical", "ahead"].includes(String(ancestry.status))) {
    throw new Error("Merge commit is not an ancestor of the default branch.");
  }
  const checksDocument = readRecord(await githubRequest(`${api}/commits/${headSha}/check-runs?per_page=100`, installationToken, request));
  const statusDocument = readRecord(await githubRequest(`${api}/commits/${headSha}/status`, installationToken, request));
  const checks = readChecks(checksDocument, statusDocument);
  const codex = checks.find((check) => /codex approval/i.test(check.name));
  if (!codex || codex.conclusion !== "success" || !codex.id) throw new Error("Exact-head Codex approval is missing.");
  if (checks.some((check) => !["success", "neutral", "skipped"].includes(check.conclusion))) {
    throw new Error("A landing check is not successful.");
  }
  const successfulChecks = checks
    .filter((check) => check.conclusion === "success")
    .sort((left, right) => left.name.localeCompare(right.name));
  return {
    actor: input.actor,
    base_sha: baseSha,
    broker_version: "unigrok-control-center-v1",
    codex_disposition: { check_id: codex.id, name: codex.name },
    head_sha: headSha,
    issued_at: now.toISOString(),
    merge_commit_sha: mergeCommitSha,
    policy_version: "cloud-control-v1",
    pull_number: input.pullNumber,
    repository: `${config.repository.owner}/${config.repository.name}`,
    required_checks: successfulChecks.map((check) => ({ conclusion: "success", name: check.name })),
    resulting_main_sha: resultingMainSha,
    schema_version: "unigrok-signed-landing-receipt-v1",
  };
}

export function signLandingReceipt(config: ReceiptSigningConfig, payload: LandingReceiptPayload): SignedLandingReceipt {
  const bytes = Buffer.from(canonicalJson(payload), "utf8");
  const signature = sign(null, bytes, createPrivateKey(config.privateKey)).toString("base64url");
  return { alg: "Ed25519", key_id: config.keyId, payload, signature };
}

export function verifyLandingReceipt(config: Pick<ReceiptSigningConfig, "keyId" | "publicKey">, receipt: SignedLandingReceipt): boolean {
  if (receipt.alg !== "Ed25519" || receipt.key_id !== config.keyId || !/^[A-Za-z0-9_-]{64,128}$/.test(receipt.signature)) return false;
  return verify(null, Buffer.from(canonicalJson(receipt.payload), "utf8"), createPublicKey(config.publicKey), Buffer.from(receipt.signature, "base64url"));
}

export function receiptPublicJwk(config: ReceiptSigningConfig): Record<string, unknown> {
  return { ...createPublicKey(config.publicKey).export({ format: "jwk" }), alg: "EdDSA", kid: config.keyId, use: "sig" };
}

function readChecks(checksDocument: Record<string, unknown> | null, statusDocument: Record<string, unknown> | null): Array<{ conclusion: string; id: number; name: string }> {
  const checks: Array<{ conclusion: string; id: number; name: string }> = [];
  for (const value of readArray(checksDocument?.check_runs)) {
    const record = readRecord(value);
    const name = safeText(record?.name, 160);
    const conclusion = record?.conclusion;
    const id = record?.id;
    if (name && typeof conclusion === "string" && Number.isSafeInteger(id)) checks.push({ conclusion, id: id as number, name });
  }
  for (const value of readArray(statusDocument?.statuses)) {
    const record = readRecord(value);
    const name = safeText(record?.context, 160);
    const conclusion = record?.state;
    const id = record?.id;
    if (name && typeof conclusion === "string" && Number.isSafeInteger(id)) checks.push({ conclusion, id: id as number, name });
  }
  if (!checks.length) throw new Error("No landing checks were found.");
  return checks;
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = readRecord(value);
  if (!record) throw new Error("Receipt contains an unsupported value.");
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

function normalizePem(value: string): string { return value.includes("\\n") ? value.replaceAll("\\n", "\n").trim() : value.trim(); }
function readRecord(value: unknown): Record<string, unknown> | null { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; }
function readArray(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function readSha(value: unknown): string | null { return isSha(value) ? value : null; }
function isSha(value: unknown): value is string { return typeof value === "string" && /^[a-f0-9]{40}$/.test(value); }
function safeText(value: unknown, max: number): string | null { return typeof value === "string" && value.trim().length > 0 && value.length <= max ? value.trim() : null; }
