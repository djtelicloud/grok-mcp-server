import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptPath = fileURLToPath(import.meta.url);
const root = path.resolve(path.dirname(scriptPath), "..");
const ignoredDirectories = new Set([".git", ".next", ".sites-runtime", ".vinext", ".wrangler", "dist", "node_modules"]);
const binaryAssetExtensions = new Set([".png"]);
const pngSignature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const forbiddenPngTextChunks = new Set(["iTXt", "tEXt", "zTXt"]);
const forbiddenPatterns = [
  ["Sites project identifier", /appgprj_/i],
  ["Sites version identifier", /appgver_/i],
  ["Sites deployment identifier", /appgdep_/i],
  ["Sites auth client field", /auth_client_id/i],
  ["Sites bypass credential field", /siwc_bypass_bearer_token/i],
  ["copied private hostname", /\.chatgpt\.site/i],
  ["GitHub personal access token", /github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9]{10,}/],
  ["GitLab personal access token", /glpat-[A-Za-z0-9_-]{10,}/],
  ["OpenAI-style secret", /\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}/],
  ["xAI-style secret", /\bxai-[A-Za-z0-9_-]{12,}/i],
  ["Google API key", /\bAIza[A-Za-z0-9_-]{25,}/],
  ["AWS access key", /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/],
  ["npm access token", /\bnpm_[A-Za-z0-9]{20,}/],
  ["PyPI access token", /\bpypi-[A-Za-z0-9_-]{20,}/],
  ["Slack access token", /\bxox[baprs]-[A-Za-z0-9-]{12,}/],
  ["Stripe live secret", /\bsk_live_[A-Za-z0-9]{16,}/],
  ["private key material", /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/],
  ["JWT-like bearer value", /\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/],
  ["non-placeholder bearer credential", /Authorization\s*:\s*Bearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+\/-]{8,}/i],
];

export function contentFindings(content) {
  const findings = [];
  for (const [label, pattern] of forbiddenPatterns) {
    if (pattern.test(content)) findings.push(label);
  }
  const emails = content.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) ?? [];
  for (const email of emails) {
    if (!/@example\.(?:com|org|net)$/i.test(email)) findings.push("personal email address");
  }
  return [...new Set(findings)];
}

export function forbiddenFileReason(relativePath) {
  const normalized = relativePath.replaceAll("\\", "/").toLowerCase();
  const basename = path.posix.basename(normalized);
  if (basename === ".env.example") return null;
  if (basename === ".env" || basename.startsWith(".env.") || basename === ".dev.vars") return "credential-bearing environment file";
  if (/\.(?:key|pem|p12|pfx|jks|keystore)$/.test(basename)) return "credential-bearing key file";
  if (/^id_(?:dsa|ecdsa|ed25519|rsa)$/.test(basename)) return "private SSH key file";
  return null;
}

export function shouldScanFileContent(relativePath) {
  return !binaryAssetExtensions.has(path.extname(relativePath).toLowerCase());
}

export function binaryAssetFindings(relativePath, content) {
  if (path.extname(relativePath).toLowerCase() !== ".png") return [];
  if (!Buffer.isBuffer(content) || content.length < pngSignature.length || !content.subarray(0, 8).equals(pngSignature)) {
    return ["invalid PNG binary asset"];
  }

  const findings = [];
  let offset = pngSignature.length;
  let sawEnd = false;
  while (offset + 12 <= content.length) {
    const length = content.readUInt32BE(offset);
    const chunkEnd = offset + 12 + length;
    if (chunkEnd > content.length) return ["invalid PNG binary asset"];
    const type = content.toString("ascii", offset + 4, offset + 8);
    const data = content.subarray(offset + 8, offset + 8 + length);
    if (forbiddenPngTextChunks.has(type)) findings.push("PNG text metadata is not allowed");
    if (type !== "IDAT") {
      for (const finding of contentFindings(printableAsciiRuns(data))) {
        findings.push(finding);
      }
    }
    offset = chunkEnd;
    if (type === "IEND") {
      sawEnd = true;
      break;
    }
  }
  if (!sawEnd || offset !== content.length) findings.push("invalid PNG binary asset");
  return [...new Set(findings)];
}

function printableAsciiRuns(content) {
  const runs = [];
  let current = "";
  for (const byte of content) {
    if (byte >= 0x20 && byte <= 0x7e) {
      current += String.fromCharCode(byte);
    } else {
      if (current.length >= 4) runs.push(current);
      current = "";
    }
  }
  if (current.length >= 4) runs.push(current);
  return runs.join("\n");
}

export async function runSafetyCheck({ allowProvisionedManifest = false, directory = root, logger = console, scanTrackedFiles = true } = {}) {
  const files = await collectFiles(directory, scanTrackedFiles);
  const failures = [];
  const manifestPath = path.join(directory, ".openai", "hosting.json");

  for (const file of files) {
    try {
      await stat(file);
    } catch (error) {
      if (error?.code === "ENOENT") continue;
      throw error;
    }
    const relative = path.relative(directory, file);
    const fileReason = forbiddenFileReason(relative);
    if (fileReason) failures.push(`${relative}: ${fileReason}`);
    if (file === scriptPath || file === manifestPath) continue;
    const content = await readFile(file);
    const findings = shouldScanFileContent(relative)
      ? contentFindings(content.toString("utf8"))
      : binaryAssetFindings(relative, content);
    for (const finding of findings) failures.push(`${relative}: ${finding}`);
  }

  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const expectedKeys = allowProvisionedManifest ? "d1,project_id,r2" : "d1,r2";
  if (manifest.d1 !== null || manifest.r2 !== null) failures.push(".openai/hosting.json: only null d1 and r2 bindings are allowed");
  if (Object.keys(manifest).sort().join(",") !== expectedKeys) failures.push(".openai/hosting.json: unexpected fields");
  if (allowProvisionedManifest) {
    if (typeof manifest.project_id !== "string" || !/^appgprj_[A-Za-z0-9]+$/.test(manifest.project_id)) failures.push(".openai/hosting.json: valid installer project_id is required");
  } else if (Object.hasOwn(manifest, "project_id")) {
    failures.push(".openai/hosting.json: project_id must be absent from the source template");
  }

  const envContent = await readFile(path.join(directory, ".env.example"), "utf8");
  for (const line of envContent.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=(.*)$/);
    if (!match) continue;
    const [, key, value] = match;
    if (/(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY|ACCESS_KEY(?:_ID)?|CLIENT_(?:ID|SECRET)|CREDENTIAL|AUTH)$/.test(key) && value.trim()) failures.push(`.env.example: ${key} must be empty`);
  }

  if (failures.length) {
    logger.error("Template safety check failed:");
    for (const failure of failures) logger.error(`- ${failure}`);
    return 1;
  }

  logger.log(`${allowProvisionedManifest ? "Deployment" : "Template"} safety check passed across ${files.length} files.`);
  return 0;
}

async function collectFiles(directory, scanTrackedFiles) {
  return scanTrackedFiles ? listRepositoryFiles(directory) : listFiles(directory);
}

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const results = [];
  for (const entry of entries) {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) results.push(...await listFiles(absolute));
    if (entry.isFile()) results.push(absolute);
  }
  return results;
}

function listRepositoryFiles(directory) {
  try {
    const gitRoot = realpathSync(execFileSync("git", ["rev-parse", "--show-toplevel"], { cwd: directory, encoding: "utf8" }).trim());
    const canonicalDirectory = realpathSync(directory);
    const relativeRoot = path.relative(gitRoot, canonicalDirectory).replaceAll("\\", "/") || ".";
    const output = execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", relativeRoot], { cwd: gitRoot, encoding: "utf8" });
    return output.split("\0").filter(Boolean).map((file) => path.join(gitRoot, file));
  } catch (error) {
    throw new Error("Template safety checks require a Git working tree with enumerable source files.", { cause: error });
  }
}

const isMain = process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
if (isMain) {
  const allowProvisionedManifest = process.argv.includes("--allow-provisioned-manifest");
  process.exitCode = await runSafetyCheck({ allowProvisionedManifest });
}
