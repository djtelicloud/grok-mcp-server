import type { Metadata } from "next";
import { headers } from "next/headers";
import { forbidden, redirect } from "next/navigation";
import ControlCenter from "../control-center";
import { chatGPTSignOutPath, requireChatGPTUser } from "../chatgpt-auth";
import { createGitHubErrorSnapshot, createUnconfiguredSnapshot } from "../lib/control-center-contract";
import {
  GitHubAuthConfigurationError,
  getControlCenterOrigin,
  getGitHubControlMode,
  loadGitHubAuthConfig,
  requestHostMatchesApplication,
} from "../lib/github-auth-config";
import { authorizeGitHubCollaborator, createInstallationCredential } from "../lib/github-app";
import { fetchGitHubControlSnapshot } from "../lib/github-control-snapshot";
import { readGitHubSession } from "../lib/github-oauth";
import { getGitHubProjectAuthorization } from "../lib/github-project-authorization";
import { safeDisplayName } from "../lib/identity-display";
import { isSiteProvisioned } from "../lib/site-provisioning";
import { getPublicConnectionConfig } from "../lib/unigrok-config";
import ControlAccessDenied from "./access-denied";
import ControlSignedOut from "./signed-out";
import GitHubControlAccessDenied from "./github-access-denied";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Contributor Control",
  robots: { index: false, follow: false },
};

export default async function ControlPage() {
  let mode: "github" | "sites";
  try {
    mode = getGitHubControlMode();
  } catch {
    return <GitHubControlAccessDenied login={null} reason="configuration" />;
  }
  if (mode === "github") return <StandaloneGitHubControl />;

  try {
    const controlCenterOrigin = getControlCenterOrigin();
    if (controlCenterOrigin) redirect(new URL("/control", controlCenterOrigin).toString());
  } catch (error) {
    if (error instanceof GitHubAuthConfigurationError) {
      return <GitHubControlAccessDenied login={null} reason="configuration" />;
    }
    throw error;
  }

  const user = await requireChatGPTUser("/control");
  const authorization = getGitHubProjectAuthorization(user.email);
  const displayName = safeDisplayName(user.fullName ?? "ChatGPT user");
  const signOutPath = chatGPTSignOutPath("/");

  if (!authorization.authorized) {
    return (
      <ControlAccessDenied
        authorization={authorization}
        displayName={displayName}
        signOutPath={signOutPath}
      />
    );
  }

  const connection = getPublicConnectionConfig();
  return (
    <ControlCenter
      authorization={authorization}
      connection={connection}
      siteProvisioned={isSiteProvisioned()}
      snapshot={createUnconfiguredSnapshot(connection.repository)}
      signOutPath={signOutPath}
      user={{ displayName }}
    />
  );
}

async function StandaloneGitHubControl() {
  let config;
  try {
    config = loadGitHubAuthConfig();
  } catch {
    return <GitHubControlAccessDenied login={null} reason="configuration" />;
  }

  const requestHeaders = await headers();
  if (!requestHostMatchesApplication(config, requestHeaders.get("host"))) {
    return <GitHubControlAccessDenied login={null} reason="configuration" />;
  }
  const session = await readGitHubSession(config, requestHeaders.get("cookie"));
  // Signed-out visitors get an explanation of the surface and one sign-in
  // action instead of a naked redirect to github.com. Authorization remains
  // the fresh server-side collaborator check below after OAuth completes.
  if (!session) return <ControlSignedOut />;

  const result = await loadStandaloneGitHubControl(config, session);
  if (result.kind === "denied") forbidden();
  if (result.kind === "unavailable") {
    return <GitHubControlAccessDenied login={session.login} reason="unavailable" />;
  }
  return (
    <ControlCenter
      authorization={result.authorization}
      connection={result.connection}
      siteProvisioned={isSiteProvisioned()}
      snapshot={result.snapshot}
      signOutPath="/auth/github/logout"
      user={{ displayName: result.authorization.githubLogin }}
    />
  );
}

async function loadStandaloneGitHubControl(
  config: ReturnType<typeof loadGitHubAuthConfig>,
  session: NonNullable<Awaited<ReturnType<typeof readGitHubSession>>>,
) {
  try {
    const credential = await createInstallationCredential(config);
    const authorization = await authorizeGitHubCollaborator(
      config,
      { id: session.id, login: session.login },
      credential.token,
    );
    if (!authorization) return { kind: "denied" } as const;

    const connection = getPublicConnectionConfig();
    let snapshot;
    try {
      snapshot = await fetchGitHubControlSnapshot(config, credential.token);
    } catch {
      snapshot = createGitHubErrorSnapshot(connection.repository);
    }
    return { authorization, connection, kind: "authorized", snapshot } as const;
  } catch {
    return { kind: "unavailable" } as const;
  }
}
