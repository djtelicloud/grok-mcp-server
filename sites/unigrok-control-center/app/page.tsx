import ControlCenter from "./control-center";
import { chatGPTSignOutPath, requireChatGPTUser } from "./chatgpt-auth";
import { createUnconfiguredSnapshot } from "./lib/control-center-contract";
import { isSiteProvisioned } from "./lib/site-provisioning";
import { getPublicConnectionConfig } from "./lib/unigrok-config";

export const dynamic = "force-dynamic";

export default async function Home() {
  const user = await requireChatGPTUser("/");
  const connection = getPublicConnectionConfig();

  return (
    <ControlCenter
      connection={connection}
      siteProvisioned={isSiteProvisioned()}
      snapshot={createUnconfiguredSnapshot(connection.repository)}
      signOutPath={chatGPTSignOutPath("/")}
      user={{ displayName: safeDisplayName(user.fullName ?? "ChatGPT user") }}
    />
  );
}

function safeDisplayName(value: string): string {
  const normalized = value
    .normalize("NFKC")
    .replace(/[\u0000-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/g, "")
    .trim()
    .slice(0, 80);
  return normalized || "ChatGPT user";
}
