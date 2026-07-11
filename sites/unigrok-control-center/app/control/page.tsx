import type { Metadata } from "next";
import ControlCenter from "../control-center";
import { chatGPTSignOutPath, requireChatGPTUser } from "../chatgpt-auth";
import { createUnconfiguredSnapshot } from "../lib/control-center-contract";
import { getGitHubProjectAuthorization } from "../lib/github-project-authorization";
import { safeDisplayName } from "../lib/identity-display";
import { isSiteProvisioned } from "../lib/site-provisioning";
import { getPublicConnectionConfig } from "../lib/unigrok-config";
import ControlAccessDenied from "./access-denied";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Contributor Control",
  robots: { index: false, follow: false },
};

export default async function ControlPage() {
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
