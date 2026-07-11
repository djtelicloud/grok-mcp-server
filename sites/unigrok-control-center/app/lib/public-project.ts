export const PUBLIC_PROJECT = {
  schemaVersion: "1.0",
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
  },
  control: {
    path: "/control",
    authentication: "chatgpt-siwc",
    authorization: "server-side-github-identity-bootstrap-binding",
    authorizationStatus: "live-github-collaborator-verification-pending",
  },
  mcp: {
    localDefault: "http://localhost:4765/mcp",
    transport: "streamable-http",
    remoteStatus: "not-deployed-oauth-pending",
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
    control: {
      path: PUBLIC_PROJECT.control.path,
      authentication: PUBLIC_PROJECT.control.authentication,
      authorization: PUBLIC_PROJECT.control.authorization,
      authorization_status: PUBLIC_PROJECT.control.authorizationStatus,
    },
    mcp: {
      local_default: PUBLIC_PROJECT.mcp.localDefault,
      transport: PUBLIC_PROJECT.mcp.transport,
      remote_status: PUBLIC_PROJECT.mcp.remoteStatus,
    },
    execution_planes: PUBLIC_PROJECT.executionPlanes,
  };
}

export function publicLlmsText(): string {
  return `# UniGrok\n\n> ${PUBLIC_PROJECT.description}\n\nUniGrok gives MCP-compatible coding agents one shared, server-side gateway to Grok. The API and CLI execution planes remain distinct; never infer model availability across planes.\n\n## Canonical resources\n- Homepage: ${PUBLIC_PROJECT.homepage}\n- Repository: ${PUBLIC_PROJECT.repository.url}\n- Architecture: ${PUBLIC_PROJECT.repository.url}/blob/main/architecture.md\n- IDE setup: ${PUBLIC_PROJECT.repository.url}/blob/main/docs/ide-setup.md\n- Public project JSON: ${PUBLIC_PROJECT.homepage}${PUBLIC_PROJECT.publicSurfaces.project}\n\n## Access boundaries\n- Public project information is available without authentication.\n- Contributor control requires ChatGPT authentication and a server-configured GitHub identity bootstrap binding. Live GitHub collaborator verification is pending.\n- The default local MCP endpoint is ${PUBLIC_PROJECT.mcp.localDefault}.\n- No remote inference endpoint is deployed; OAuth-protected deployment is pending.\n- Never send an xAI API key to this website or embed it in an IDE MCP configuration.\n`;
}
