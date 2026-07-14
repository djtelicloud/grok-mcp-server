import Link from "next/link";
import { PUBLIC_PROJECT } from "./lib/public-project";

export default function Forbidden() {
  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="access-title">
        <Link className="access-brand" href="/"><span>UG</span> UniGrok</Link>
        <p className="public-kicker"><span /> Contributor boundary</p>
        <h1 id="access-title">Contributor access was not confirmed.</h1>
        <p className="access-lede">
          GitHub did not confirm a current write, maintain, or admin role for this repository.
          Public read access is not sufficient.
        </p>
        <div className="access-notice">
          <strong>No protected project data was disclosed.</strong>
          <p>Authorization is checked live for every control-center request.</p>
        </div>
        <div className="public-actions">
          <a className="public-primary" href="/auth/github/login?return_to=%2Fcontrol">Try another GitHub account</a>
          <a className="public-secondary" href={PUBLIC_PROJECT.repository.url}>Open public project ↗</a>
        </div>
      </section>
    </main>
  );
}
