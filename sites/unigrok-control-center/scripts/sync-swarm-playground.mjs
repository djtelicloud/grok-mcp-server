import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDir, "../../..");
const sourceDir = resolve(repositoryRoot, "mcp_ui");
const targetDir = resolve(scriptDir, "../public/swarm");

await mkdir(targetDir, { recursive: true });

const html = (await readFile(resolve(sourceDir, "swarm.html"), "utf8"))
  .replace('href="./index.html"', 'href="/"')
  .replace("Contributor lab · verified optimization", "Public lab · client-side preview");

await Promise.all([
  writeFile(resolve(targetDir, "index.html"), html),
  writeFile(
    resolve(targetDir, "swarm.js"),
    await readFile(resolve(sourceDir, "swarm.js"), "utf8"),
  ),
  writeFile(
    resolve(targetDir, "swarm-sample.json"),
    await readFile(resolve(sourceDir, "swarm-sample.json"), "utf8"),
  ),
  // Shared design tokens: the page links ./tokens.css so the public copy
  // renders with the same UniGrok identity as the local surfaces.
  writeFile(
    resolve(targetDir, "tokens.css"),
    await readFile(resolve(sourceDir, "tokens.css"), "utf8"),
  ),
]);

console.log("Synchronized public Swarm Playground assets.");
