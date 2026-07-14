import Link from "next/link";
import { PUBLIC_PROJECT } from "../lib/public-project";

export default function ContributePage() {
  return (
    <main className="public-shell">
      <header className="public-nav">
        <Link className="public-brand" href="/"><span className="public-brand-mark">UG</span><span><strong>UniGrok</strong><small>Contributor onboarding</small></span></Link>
        <nav aria-label="Contributor navigation"><Link href="/">Project</Link><a href={PUBLIC_PROJECT.repository.url}>GitHub</a></nav>
        <a className="public-nav-action" href={PUBLIC_PROJECT.control.origin}>Contributor login</a>
      </header>
      <section className="public-hero public-contributor-hero">
        <div className="public-hero-copy">
          <p className="public-kicker"><span /> Contributor access</p>
          <h1>Build in public.<br /><em>Operate by role.</em></h1>
          <p className="public-lede">Anyone can learn, run, and propose changes. The protected control center is limited to GitHub users with an approved role on the UniGrok repository.</p>
          <div className="public-actions"><a className="public-primary" href={PUBLIC_PROJECT.control.origin}>Sign in with GitHub <span>→</span></a><a className="public-secondary" href={`${PUBLIC_PROJECT.repository.url}/blob/main/CONTRIBUTING.md`}>Read contribution guide</a></div>
        </div>
      </section>
      <section className="public-section">
        <div className="public-capability-grid">
          <article><b>01</b><h3>Learn and run</h3><p>Read the architecture, start the local gateway, and verify your IDE connection without giving this site an xAI key.</p></article>
          <article><b>02</b><h3>Propose work</h3><p>Open an issue or pull request. Codex reviews evidence, tests the exact commit, and owns integration into main.</p></article>
          <article><b>03</b><h3>Receive access</h3><p>A maintainer grants a GitHub repository role. Control access starts at write and is rechecked on every protected request.</p></article>
        </div>
      </section>
    </main>
  );
}
