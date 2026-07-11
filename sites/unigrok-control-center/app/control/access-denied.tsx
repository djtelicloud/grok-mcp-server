import Link from "next/link";
import type { GitHubProjectAuthorization } from "../lib/github-project-authorization";
import { PUBLIC_PROJECT } from "../lib/public-project";

type DeniedAuthorization = Extract<
  GitHubProjectAuthorization,
  { authorized: false }
>;

export default function ControlAccessDenied({
  authorization,
  displayName,
  signOutPath,
}: {
  authorization: DeniedAuthorization;
  displayName: string;
  signOutPath: string;
}) {
  const explanation =
    authorization.reason === "not-configured"
      ? "The server-side GitHub project-role adapter has not been configured, so access is denied by default."
      : authorization.reason === "invalid-configuration"
        ? "The project-role configuration could not be validated, so access is denied by default."
        : "This ChatGPT identity has no approved GitHub project-role binding.";

  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="access-title">
        <Link className="access-brand" href="/"><span>UG</span> UniGrok</Link>
        <p className="public-kicker"><span /> Contributor boundary</p>
        <h1 id="access-title">The control center is locked.</h1>
        <p className="access-lede">{explanation}</p>

        <div className="access-checks" aria-label="Control access checks">
          <div className="passed"><b aria-hidden="true">✓</b><span><small>AUTHENTICATION</small><strong>ChatGPT identity confirmed</strong><em>{displayName}</em></span></div>
          <div className="denied"><b aria-hidden="true">×</b><span><small>PROJECT AUTHORIZATION</small><strong>GitHub role not confirmed</strong><em>No control-center data was disclosed</em></span></div>
        </div>

        <div className="access-notice">
          <strong>Why two checks?</strong>
          <p>Sign in with ChatGPT identifies the viewer. It does not prove that the viewer is an administrator or contributor to {PUBLIC_PROJECT.repository.name}. The current control gate uses a server-configured GitHub identity bootstrap binding; live GitHub collaborator verification is pending.</p>
        </div>

        <div className="public-actions">
          <Link className="public-primary" href="/">Return to public site</Link>
          <a className="public-secondary" href={PUBLIC_PROJECT.repository.url}>Open GitHub project ↗</a>
        </div>
        <a className="access-signout" href={signOutPath}>Use a different ChatGPT identity</a>
      </section>
    </main>
  );
}
