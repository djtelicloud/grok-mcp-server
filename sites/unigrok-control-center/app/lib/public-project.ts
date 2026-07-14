export const PUBLIC_PROJECT = {
  schemaVersion: "1.1",
  name: "UniGrok",
  description: "A local-first universal MCP gateway for xAI Grok models.",
  homepage: "https://grokmcp.org",
  repository: {
    name: "djtelicloud/grok-mcp-server",
    url: "https://github.com/djtelicloud/grok-mcp-server",
    cloneUrl: "https://github.com/djtelicloud/grok-mcp-server.git",
  },
  publicSurfaces: {
    project: "/api/public/v1/project",
    discovery: "/.well-known/unigrok.json",
    llms: "/llms.txt",
    okf: "/docs/okf/okf-manifest.json",
    contribute: "/contribute",
  },
  control: {
    origin: "https://control.grokmcp.org",
    authentication: "github-oauth-app",
    authorization: "fresh-server-side-github-repository-role-check",
    minimumRole: "write",
  },
  mcp: {
    localDefault: "http://localhost:4765/mcp",
    privateRemote: "https://mcp.grokmcp.org/mcp",
    transport: "streamable-http",
    remoteStatus: "private-oauth-api-plane",
  },
  executionPlanes: ["xai-api", "grok-cli"],
} as const;

export function publicProjectDocument() {
  return {
    schema_version: PUBLIC_PROJECT.schemaVersion,
    name: PUBLIC_PROJECT.name,
    description: PUBLIC_PROJECT.description,
    homepage: PUBLIC_PROJECT.homepage,
    repository: PUBLIC_PROJECT.repository,
    public_surfaces: PUBLIC_PROJECT.publicSurfaces,
    documentation: {
      architecture: `${PUBLIC_PROJECT.repository.url}/blob/main/architecture.md`,
      ide_setup: `${PUBLIC_PROJECT.repository.url}/blob/main/docs/ide-setup.md`,
      contributing: `${PUBLIC_PROJECT.repository.url}/blob/main/CONTRIBUTING.md`,
      okf_manifest: `${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.okf}`,
    },
    control: {
      origin: PUBLIC_PROJECT.control.origin,
      authentication: PUBLIC_PROJECT.control.authentication,
      authorization: PUBLIC_PROJECT.control.authorization,
      minimum_repository_role: PUBLIC_PROJECT.control.minimumRole,
      status: publicControlStatus(),
    },
    mcp: {
      local_default: PUBLIC_PROJECT.mcp.localDefault,
      private_remote: PUBLIC_PROJECT.mcp.privateRemote,
      transport: PUBLIC_PROJECT.mcp.transport,
      remote_status: PUBLIC_PROJECT.mcp.remoteStatus,
    },
    execution_planes: PUBLIC_PROJECT.executionPlanes,
    public_access_policy: {
      project_information: "unauthenticated",
      remote_mcp: "not-public",
      credentials: "never-accepted-by-public-site",
    },
  };
}

export function publicDiscoveryDocument() {
  return {
    schema_version: PUBLIC_PROJECT.schemaVersion,
    name: PUBLIC_PROJECT.name,
    homepage: PUBLIC_PROJECT.homepage,
    repository: PUBLIC_PROJECT.repository.url,
    project: `${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.project}`,
    llms: `${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.llms}`,
    okf: `${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.okf}`,
    contribute: `${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.contribute}`,
    control: PUBLIC_PROJECT.control.origin,
    private_mcp: PUBLIC_PROJECT.mcp.privateRemote,
  };
}

function publicControlStatus(): "configured" | "deployment-pending" {
  return process.env.CONTROL_CENTER_ORIGIN === PUBLIC_PROJECT.control.origin
    ? "configured"
    : "deployment-pending";
}

export function publicLlmsText(): string {
  return `# UniGrok\n\n> ${PUBLIC_PROJECT.description}\n\nUniGrok gives MCP-compatible coding agents one shared, server-side gateway to Grok. The API and CLI execution planes remain distinct; never infer model availability across planes.\n\n## Canonical resources\n- Homepage: ${PUBLIC_PROJECT.homepage}\n- Repository: ${PUBLIC_PROJECT.repository.url}\n- Architecture: ${PUBLIC_PROJECT.repository.url}/blob/main/architecture.md\n- IDE setup: ${PUBLIC_PROJECT.repository.url}/blob/main/docs/ide-setup.md\n- Contribute: ${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.contribute}\n- Public project JSON: ${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.project}\n- OKF knowledge bundle: ${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.okf}\n\n## Access boundaries\n- Public project information is available without authentication.\n- Contributor control uses GitHub login and a fresh server-side repository role check on every protected request. The minimum accepted role is ${PUBLIC_PROJECT.control.minimumRole}.\n- The default local MCP endpoint is ${PUBLIC_PROJECT.mcp.localDefault}.\n- The private API-plane remote MCP is ${PUBLIC_PROJECT.mcp.privateRemote}. It uses OAuth PKCE, short-lived scoped tokens, and live repository-access introspection.\n- No unauthenticated remote MCP or inference endpoint is published. Public machine-readable project information is deliberately separate from contributor controls.\n- Never send an xAI API key to this website or embed it in an IDE MCP configuration.\n`;
}
