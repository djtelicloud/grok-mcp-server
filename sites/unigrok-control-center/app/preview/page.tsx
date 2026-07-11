import { notFound } from "next/navigation";
import ControlCenter from "../control-center";
import { createUnconfiguredSnapshot } from "../lib/control-center-contract";
import { isSiteProvisioned } from "../lib/site-provisioning";
import { getPublicConnectionConfig } from "../lib/unigrok-config";

export const dynamic = "force-dynamic";

export default function PreviewPage() {
  if (process.env.NODE_ENV === "production") notFound();
  const connection = getPublicConnectionConfig();

  return (
    <ControlCenter
      connection={connection}
      previewMode
      signOutPath="/"
      siteProvisioned={isSiteProvisioned()}
      snapshot={createUnconfiguredSnapshot(connection.repository)}
      user={{ displayName: "Template Preview" }}
    />
  );
}
