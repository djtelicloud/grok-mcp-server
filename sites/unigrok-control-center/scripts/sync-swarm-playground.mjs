import { mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const targetDir = resolve(scriptDir, "../public/swarm");

await rm(targetDir, { recursive: true, force: true });
await mkdir(targetDir, { recursive: true });

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="refresh" content="0; url=/control" />
    <title>UniGrok Contributor Control</title>
  </head>
  <body>
    <p>Swarm is an Insider Console workflow. <a href="/control">Continue to GitHub-gated contributor control.</a></p>
  </body>
</html>
`;

await writeFile(resolve(targetDir, "index.html"), html);

console.log("Synchronized the public Swarm gate to Contributor Control.");
