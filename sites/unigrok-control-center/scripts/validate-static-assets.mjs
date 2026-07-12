import { readFile, readdir } from "node:fs/promises";
import { relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

async function listFiles(root, directory = root) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listFiles(root, path)));
      continue;
    }
    if (!entry.isFile()) {
      throw new Error(`Unsupported public asset type: ${relative(root, path)}`);
    }
    files.push(relative(root, path));
  }

  return files.sort();
}

export async function validateStaticAssets(publicDirectory, clientDirectory) {
  const publicRoot = resolve(publicDirectory);
  const clientRoot = resolve(clientDirectory);
  const files = await listFiles(publicRoot);

  for (const file of files) {
    const source = await readFile(resolve(publicRoot, file));
    let built;
    try {
      built = await readFile(resolve(clientRoot, file));
    } catch (error) {
      if (error?.code === "ENOENT") {
        throw new Error(`Missing built static asset: ${file}`);
      }
      throw error;
    }
    if (!source.equals(built)) {
      throw new Error(`Stale built static asset: ${file}`);
    }
  }

  return files.length;
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : null;
if (invokedPath === import.meta.url) {
  const [publicDirectory, clientDirectory] = process.argv.slice(2);
  if (!publicDirectory || !clientDirectory) {
    console.error("Usage: node validate-static-assets.mjs PUBLIC_DIR CLIENT_DIR");
    process.exitCode = 64;
  } else {
    try {
      const count = await validateStaticAssets(publicDirectory, clientDirectory);
      console.log(`Validated ${count} public assets against dist/client.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`Static asset validation failed: ${message}`);
      process.exitCode = 1;
    }
  }
}
