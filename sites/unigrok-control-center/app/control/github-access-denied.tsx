import Link from "next/link";
import { PUBLIC_PROJECT } from "../lib/public-project";

export default function GitHubControlAccessDenied({
  login,
  reason,
}: {
  login: string | null;
  reason: "configuration" | "not-authorized" | "unavailable";
}) {
  const explanation =
    reason === "not-authorized"
      ? "GitHub did not confirm a contributor-level role for this account. Public read access is not sufficient."
      : reason === "configuration"
        ? "The GitHub App authentication boundary is incomplete, so access is denied by default."
        : "GitHub could not be checked right now, so no protected project data is shown.";

  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="access-title">
        <Link className="access-brand" href="/"><span>UG</span> UniGrok</Link>
        <p className="public-kicker"><span /> Contributor boundary</p>
        <h1 id="access-title">The control center is locked.</h1>
        <p className="access-lede">{explanation}</p>
        <div className="access-checks" aria-label="Control access checks">
          <div className={login ? "passed" : "denied"}><b aria-hidden="true">{login ? "✓" : "×"}</b><span><small>GITHUB IDENTITY</small><strong>{login ? `@${login}` : "No valid session"}</strong><em>Signed, HttpOnly session</em></span></div>
          <div className="denied"><b aria-hidden="true">×</b><span><small>FRESH PROJECT AUTHORIZATION</small><strong>Contributor role not confirmed</strong><em>No control-center data was disclosed</em></span></div>
        </div>
        <div className="access-notice">
          <strong>Authorization is checked on every request.</strong>
          <p>Only GitHub roles with write, maintain, or admin access to {PUBLIC_PROJECT.repository.name} qualify. Removing that role revokes control access without waiting for the browser session to expire.</p>
        </div>
        <div className="public-actions">
          <a className="public-primary" href="/auth/github/login?return_to=%2Fcontrol">Sign in with GitHub</a>
          <a className="public-secondary" href={PUBLIC_PROJECT.repository.url}>Open public project ↗</a>
        </div>
        {login && <form action="/auth/github/logout" method="post"><button className="access-signout" type="submit">Use a different GitHub account</button></form>}
      </section>
    </main>
  );
}
