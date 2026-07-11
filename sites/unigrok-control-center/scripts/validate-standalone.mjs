import { access, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const requiredPaths = [
  ".next/standalone/server.js",
  ".next/standalone/package.json",
  ".next/static",
  "public",
];

for (const relativePath of requiredPaths) {
  const absolutePath = path.join(projectRoot, relativePath);
  await access(absolutePath).catch(() => {
    throw new Error(`Standalone build is missing ${relativePath}`);
  });
}

const serverPath = path.join(projectRoot, ".next/standalone/server.js");
const serverStat = await stat(serverPath);
if (!serverStat.isFile() || serverStat.size === 0) {
  throw new Error("Standalone server.js is not a non-empty file");
}

const packageDocument = JSON.parse(
  await readFile(path.join(projectRoot, ".next/standalone/package.json"), "utf8"),
);
if (!packageDocument || typeof packageDocument !== "object") {
  throw new Error("Standalone package.json is not a JSON object");
}

console.log("Standalone Next.js artifact validated.");
