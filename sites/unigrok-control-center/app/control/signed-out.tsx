import Link from "next/link";
import { PUBLIC_PROJECT } from "../lib/public-project";

/**
 * Anonymous view of /control. Gated surfaces stay visible with a plain
 * explanation and one sign-in action — never a naked redirect that strands a
 * visitor on github.com with no context. No protected data is rendered here;
 * authorization stays a fresh server-side collaborator check after OAuth.
 */
export default function ControlSignedOut() {
  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="control-welcome-title">
        <Link className="access-brand" href="/"><span>UG</span> UniGrok</Link>
        <p className="public-kicker"><span /> Contributor control center</p>
        <h1 id="control-welcome-title">Live project operations, for contributors.</h1>
        <p className="access-lede">
          The control center shows live GitHub evidence for {PUBLIC_PROJECT.repository.name}:
          open pull requests with their exact checks, default-branch status, recent
          deployments, and the connection guide for a local UniGrok gateway.
        </p>
        <div className="access-notice">
          <strong>Who gets in, and how it is checked.</strong>
          <p>
            Sign in with GitHub, and every request re-verifies your collaborator role
            (write, maintain, or admin) against the repository — server-side, on each visit.
            There is nothing to configure and no separate account to create. Public
            project information stays available without any sign-in.
          </p>
        </div>
        <div className="public-actions">
          <a className="public-primary" href="/auth/github/login?return_to=%2Fcontrol">Sign in with GitHub</a>
          <Link className="public-secondary" href="/">Back to the project site</Link>
          <a className="public-secondary" href={PUBLIC_PROJECT.repository.url}>Open public repository ↗</a>
        </div>
      </section>
    </main>
  );
}
