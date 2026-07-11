export type ConnectionMode = "local" | "tunnel" | "unconfigured";

export type PublicConnectionConfig = {
  configured: boolean;
  connectionMode: ConnectionMode;
  endpointLabel: string;
  localBaseUrl: string | null;
  repository: string | null;
  repositoryUrl: string | null;
  tunnelProfile: string | null;
};

export function getPublicConnectionConfig(): PublicConnectionConfig {
  const connectionMode = readConnectionMode();
  const repository = readRepository();
  const localUrl = readLocalUrl();
  const tunnelProfile = readTunnelProfile();
  const configured =
    (connectionMode === "local" && localUrl !== null) ||
    (connectionMode === "tunnel" && tunnelProfile !== null);

  return {
    configured,
    connectionMode,
    endpointLabel:
      connectionMode === "local" && localUrl
        ? `${localUrl.hostname}:${localUrl.port || "80"}`
        : connectionMode === "tunnel" && tunnelProfile
          ? `Tunnel profile: ${tunnelProfile}`
          : "Not configured",
    localBaseUrl: localUrl ? localUrl.origin : null,
    repository,
    repositoryUrl: repository ? `https://github.com/${repository}` : null,
    tunnelProfile,
  };
}

function readConnectionMode(): ConnectionMode {
  const value = readText("UNIGROK_CONNECTION_MODE").toLowerCase();
  if (value === "local" || value === "tunnel") return value;
  return "unconfigured";
}

function readRepository(): string | null {
  const value = readText("GITHUB_REPOSITORY");
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value)) return null;
  return value;
}

function readLocalUrl(): URL | null {
  const value = readText("UNIGROK_LOCAL_BASE_URL");
  if (!value || value.length > 200) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" || url.username || url.password || url.search || url.hash) return null;
    if (url.pathname !== "/" && url.pathname !== "") return null;
    const host = url.hostname.toLowerCase();
    if (host !== "localhost" && host !== "127.0.0.1" && host !== "[::1]" && host !== "::1") return null;
    return url;
  } catch {
    return null;
  }
}

function readTunnelProfile(): string | null {
  const value = readText("UNIGROK_TUNNEL_PROFILE");
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(value)) return null;
  return value;
}

function readText(key: string): string {
  const value = process.env[key];
  return typeof value === "string" ? value.trim() : "";
}
