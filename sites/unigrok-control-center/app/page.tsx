import Link from "next/link";
import { PUBLIC_PROJECT } from "./lib/public-project";

const quickStart = `git clone ${PUBLIC_PROJECT.repository.cloneUrl}\ncd grok-mcp-server\nuv run python main.py init\n# Choose an API or CLI credential path; then:\ndocker compose up --build -d`;

export default function Home() {
  return (
    <main className="public-shell">
      <header className="public-nav">
        <Link className="public-brand" href="/" aria-label="UniGrok home">
          <span className="public-brand-mark" aria-hidden="true">UG</span>
          <span><strong>UniGrok</strong><small>Grok MCP gateway</small></span>
        </Link>
        <nav aria-label="Public navigation">
          <a href="#how-it-works">How it works</a>
          <a href="#start">Get started</a>
          <a href="/contribute">Contribute</a>
          <a href={PUBLIC_PROJECT.repository.url}>GitHub</a>
        </nav>
        <a className="public-nav-action" href="/control">Contributor control</a>
      </header>

      <section className="public-hero">
        <div className="public-hero-copy">
          <p className="public-kicker"><span /> Open source · local first · MCP native</p>
          <h1>One Grok gateway.<br /><em>Every coding agent.</em></h1>
          <p className="public-lede">
            UniGrok lets Codex, Claude, VS Code, and other MCP clients share a
            server-side Grok connection without copying your xAI credential into
            every editor.
          </p>
          <div className="public-actions">
            <a className="public-primary" href={PUBLIC_PROJECT.repository.url}>Explore the project <span aria-hidden="true">↗</span></a>
            <a className="public-secondary" href="#start">Run it locally <span aria-hidden="true">↓</span></a>
          </div>
          <div className="public-proof" aria-label="Project principles">
            <span><b>01</b> One server-side credential boundary</span>
            <span><b>02</b> API and subscription CLI planes stay distinct</span>
            <span><b>03</b> Codex-owned integration and release gates</span>
          </div>
        </div>

        <div className="public-terminal" aria-label="Example local UniGrok command session">
          <div className="public-terminal-bar"><span /><span /><span /><b>example · local command session</b></div>
          <div className="public-terminal-body">
            <p><i>$</i> curl -fsS localhost:4765/healthz</p>
            <p className="public-terminal-ok">✓ example response: {`{"status":"healthy"}`}</p>
            <p><i>$</i> codex mcp get grok</p>
            <div className="public-terminal-card">
              <span>transport</span><strong>Streamable HTTP</strong>
              <span>endpoint</span><strong>localhost:4765/mcp</strong>
              <span>tools</span><strong>agent + research jobs</strong>
              <span>credential</span><strong className="public-terminal-ok">server-side only</strong>
            </div>
            <p className="public-terminal-note"># each client sends its own X-Client-ID</p>
          </div>
        </div>
      </section>

      <p className="public-contract-note">Published route contract · not a live runtime probe</p>
      <section className="public-status" aria-label="Published route contract, not live runtime status">
        <article><span className="public-status-dot ready" /><div><small>PUBLIC PROJECT INFO</small><strong>Static metadata available</strong></div></article>
        <article><span className="public-status-dot gated" /><div><small>CONTROL CENTER</small><strong>GitHub role-gated</strong></div></article>
        <article><span className="public-status-dot planned" /><div><small>REMOTE MCP</small><strong>Private review pending · public MCP deferred</strong></div></article>
      </section>

      <section className="public-section" id="how-it-works">
        <div className="public-section-heading">
          <p className="public-kicker"><span /> One service, clear trust zones</p>
          <h2>Local power without making<br />your laptop the product.</h2>
          <p>The public site teaches the project. The protected control center is for approved contributors. UniGrok itself keeps credentials and model execution behind an MCP boundary.</p>
        </div>
        <div className="public-capability-grid">
          <article><b>01</b><h3>Shared MCP gateway</h3><p>Connect multiple IDE agents to one stable endpoint with per-client attribution and server-held credentials.</p><code>POST /mcp</code></article>
          <article><b>02</b><h3>Two execution planes</h3><p>The xAI API plane and Grok CLI subscription plane have separate catalogs, sessions, costs, and failover rules.</p><code>api ≠ cli</code></article>
          <article><b>03</b><h3>Supervised collaboration</h3><p>Agents and humans can propose changes while deterministic tests and Codex-owned landing keep main coherent.</p><code>review → verify → land</code></article>
        </div>
      </section>

      <section className="public-flow" aria-label="UniGrok system flow">
        <div><span>IDE agents</span><strong>Codex · Claude · VS Code</strong></div><i aria-hidden="true">→</i>
        <div className="active"><span>UniGrok MCP</span><strong>Routing · sessions · policy</strong></div><i aria-hidden="true">→</i>
        <div><span>Grok planes</span><strong>xAI API · local CLI</strong></div>
      </section>

      <section className="public-section public-start" id="start">
        <div className="public-section-heading">
          <p className="public-kicker"><span /> Start with the real boundary</p>
          <h2>Run locally. Verify directly.<br />Then connect your IDE.</h2>
        </div>
        <p className="public-start-note">Initialization prints the supported xAI API and SuperGrok CLI credential paths. Complete at least one before expecting real agent calls to succeed.</p>
        <div className="public-start-grid">
          <pre><code>{quickStart}</code></pre>
          <div className="public-start-links">
            <a href={`${PUBLIC_PROJECT.repository.url}#quick-start`}><span>Quick start</span><strong>Install and start UniGrok <b>↗</b></strong></a>
            <a href={`${PUBLIC_PROJECT.repository.url}/blob/main/docs/ide-setup.md`}><span>IDE setup</span><strong>Connect Codex and other clients <b>↗</b></strong></a>
            <a href={`${PUBLIC_PROJECT.repository.url}/blob/main/architecture.md`}><span>Architecture</span><strong>Understand planes and trust zones <b>↗</b></strong></a>
          </div>
        </div>
      </section>

      <section className="public-machine">
        <div><p className="public-kicker"><span /> Built for people and agents</p><h2>Public project context,<br />in machine-readable form.</h2></div>
        <div className="public-machine-links">
          <a href="/llms.txt"><code>/llms.txt</code><span>Concise agent orientation →</span></a>
          <a href="/.well-known/unigrok.json"><code>/.well-known/unigrok.json</code><span>Discovery contract →</span></a>
          <a href="/api/public/v1/project"><code>/api/public/v1/project</code><span>Versioned metadata →</span></a>
        </div>
      </section>

      <section className="public-contribute">
        <div><p className="public-kicker"><span /> Help build the gateway</p><h2>Contributors get a control plane.<br />Visitors get a clear front door.</h2><p>GitHub login establishes contributor identity. Every protected request performs a fresh server-side role check against this repository before returning project data.</p></div>
        <div className="public-actions"><a className="public-primary" href="/contribute">Become a contributor <span aria-hidden="true">→</span></a><a className="public-secondary" href={`${PUBLIC_PROJECT.repository.url}/issues`}>View open issues</a></div>
      </section>

      <footer className="public-footer"><Link className="public-brand" href="/"><span className="public-brand-mark" aria-hidden="true">UG</span><span><strong>UniGrok</strong><small>Universal Grok MCP</small></span></Link><p>Open-source infrastructure for supervised, multi-agent development.</p><div><a href={PUBLIC_PROJECT.repository.url}>GitHub</a><a href="/contribute">Contribute</a><a href="/llms.txt">llms.txt</a><a href="/control">Control</a></div></footer>
    </main>
  );
}
