import hostingManifest from "../../.openai/hosting.json";

export function isSiteProvisioned(): boolean {
  const manifest = hostingManifest as Record<string, unknown>;
  const projectIdPattern = new RegExp(`^${["appgprj", "_"].join("")}[A-Za-z0-9]+$`);
  return typeof manifest.project_id === "string" && projectIdPattern.test(manifest.project_id);
}
