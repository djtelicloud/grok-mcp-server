"use client";

import { useEffect, useRef, useState } from "react";
import type { ControlCenterSnapshot, IntegrationState } from "./lib/control-center-contract";
import type { GitHubProjectAuthorization } from "./lib/github-project-authorization";
import type { PublicConnectionConfig } from "./lib/unigrok-config";

type IconName =
  | "activity"
  | "arrow"
  | "branch"
  | "check"
  | "chevron"
  | "clock"
  | "cloud"
  | "code"
  | "deploy"
  | "external"
  | "github"
  | "home"
  | "lock"
  | "menu"
  | "pr"
  | "review"
  | "search"
  | "settings"
  | "shield"
  | "spark"
  | "terminal"
  | "x";

type PanelName = "connection" | "deployments" | "grok-review" | "pull-requests" | "repository" | "review" | "runtime" | "settings" | null;
type WizardMode = "local" | "tunnel";

type ControlCenterProps = {
  authorization: Extract<GitHubProjectAuthorization, { authorized: true }>;
  connection: PublicConnectionConfig;
  previewMode?: boolean;
  signOutPath: string;
  siteProvisioned: boolean;
  snapshot: ControlCenterSnapshot;
  user: { displayName: string };
};

type CommandItem = {
  action: () => void;
  detail: string;
  icon: IconName;
  label: string;
};

const secureTunnelGuide = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels";
const sitesGuide = "https://learn.chatgpt.com/docs/sites";
const guardrails = [
  "Identity and fresh project authorization stay separate",
  "Missing, revoked, or public-read-only GitHub access denies control access",
  "No credential entry or browser-side secret storage",
  "Localhost is never presented as publicly reachable",
];

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const common = {
    "aria-hidden": true,
    fill: "none",
    height: size,
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.8,
    viewBox: "0 0 24 24",
    width: size,
  };

  const paths: Record<IconName, React.ReactNode> = {
    activity: <path d="M3 12h4l2-7 4 14 2-7h6" />,
    arrow: <><path d="M5 12h14" /><path d="m14 7 5 5-5 5" /></>,
    branch: <><circle cx="6" cy="5" r="2" /><circle cx="18" cy="6" r="2" /><circle cx="6" cy="19" r="2" /><path d="M6 7v10" /><path d="M8 9h5a5 5 0 0 0 5-5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    cloud: <path d="M17.5 19H7a5 5 0 0 1-.6-9.96A6.5 6.5 0 0 1 18.8 11 4 4 0 0 1 17.5 19Z" />,
    code: <><path d="m8 9-3 3 3 3" /><path d="m16 9 3 3-3 3" /><path d="m14 6-4 12" /></>,
    deploy: <><path d="M12 3v12" /><path d="m7 8 5-5 5 5" /><path d="M5 14v5h14v-5" /></>,
    external: <><path d="M14 5h5v5" /><path d="m19 5-9 9" /><path d="M18 13v6H5V6h6" /></>,
    github: <><path d="M9 19c-4.5 1.4-4.5-2.4-6.3-3" /><path d="M15 22v-3.5c0-1 .1-1.7-.5-2.3 3.2-.4 6.5-1.6 6.5-7A5.5 5.5 0 0 0 19.5 5 5 5 0 0 0 19.3 1S18.1.6 15 2.5a14 14 0 0 0-6 0C5.9.6 4.7 1 4.7 1a5 5 0 0 0-.2 4A5.5 5.5 0 0 0 3 9.2c0 5.4 3.3 6.6 6.5 7-.6.6-.6 1.4-.5 2.3V22" /></>,
    home: <><path d="m3 11 9-8 9 8" /><path d="M5 10v10h14V10" /><path d="M9 20v-6h6v6" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>,
    menu: <><path d="M4 7h16" /><path d="M4 12h16" /><path d="M4 17h16" /></>,
    pr: <><circle cx="6" cy="5" r="2" /><circle cx="6" cy="19" r="2" /><circle cx="18" cy="19" r="2" /><path d="M6 7v10" /><path d="M18 17v-5a3 3 0 0 0-3-3H9" /><path d="m12 6-3 3 3 3" /></>,
    review: <><path d="m12 3 1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8L12 3Z" /><path d="m19 14 .8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8L19 14Z" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></>,
    spark: <><path d="m12 3 1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8L12 3Z" /><path d="m5 15 .7 1.8 1.8.7-1.8.7L5 20l-.7-1.8-1.8-.7 1.8-.7L5 15Z" /></>,
    terminal: <><path d="m5 7 4 4-4 4" /><path d="M11 17h8" /></>,
    x: <><path d="m6 6 12 12" /><path d="M18 6 6 18" /></>,
  };

  return <svg {...common}>{paths[name]}</svg>;
}

function StatusPill({ tone, children }: { tone: "green" | "amber" | "blue" | "violet" | "muted"; children: React.ReactNode }) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

function PullRequestSurface({ snapshot }: { snapshot: ControlCenterSnapshot }) {
  const { items, message, state } = snapshot.pullRequests;
  if (state !== "ready" || items.length === 0) {
    return (
      <div className={`integration-empty integration-${state}`}>
        <span className="integration-empty-icon"><Icon name="pr" size={25} /></span>
        <StatusPill tone={integrationStateTone(state)}>{integrationStateLabel(state)}</StatusPill>
        <h3>{state === "ready" ? "No open pull requests" : "PR data is not connected"}</h3>
        <p>{message}</p>
      </div>
    );
  }

  return (
    <div className="pr-table">
      <div className="pr-table-head" aria-hidden="true"><span>PR</span><span>Title</span><span>Author</span><span>Checks</span><span>Review</span></div>
      <ul className="pr-list" aria-label="Open pull requests">
        {items.slice(0, 5).map((item) => {
          const url = safePullRequestUrl(item.url);
          return (
            <li className="pr-row" key={item.number}>
            <span className="pr-number">#{boundedInteger(item.number)}</span>
            <span className="pr-title-cell">{url ? <a href={url} target="_blank" rel="noreferrer">{item.title}</a> : <strong>{item.title}</strong>}<small>{item.releaseImpact === "blocking" ? "Explicit release blocker" : "Informational to release"}</small></span>
            <span className="author-cell"><span className="mini-avatar">{displayInitials(item.author)}</span><span>{item.author}</span></span>
            <span className="thread-count"><b>{boundedInteger(item.checksPassed)}/{boundedInteger(item.checksTotal)}</b><span>checks</span></span>
            <StatusPill tone={reviewStateTone(item.reviewState)}>{reviewStateLabel(item.reviewState)}</StatusPill>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function GrokReviewSurface({ snapshot }: { snapshot: ControlCenterSnapshot }) {
  const { findings, message, score, state, verdict } = snapshot.grokReview;
  const hasResult = state === "ready" && score !== null;
  const hasFindings = state === "ready" && findings.length > 0;
  const boundedScore = score === null ? null : Math.min(100, Math.max(0, boundedInteger(score)));

  return (
    <div className="review-body">
      <div className={hasResult ? "score-ring" : "score-ring unconfigured"} aria-label={hasResult ? `Grok review score ${boundedScore} out of 100` : `Grok review ${integrationStateLabel(state).toLowerCase()}`}>
        <div><strong>{hasResult ? boundedScore : "—"}</strong><span>{hasResult ? "/ 100" : "No score"}</span></div>
      </div>
      <div className="review-summary">
        <small>Review state</small>
        <h3>{state === "ready" ? verdict ?? "Review received" : integrationStateLabel(state)}</h3>
        {hasFindings ? (
          <ul>{findings.slice(0, 3).map((finding) => <li key={`${finding.evidencePath}-${finding.title}`}><span className={`finding-dot finding-${finding.severity}`} /> {finding.title}</li>)}</ul>
        ) : (
          <p className="integration-message">{message}</p>
        )}
      </div>
    </div>
  );
}

function GitHubEvidenceSurface({ snapshot }: { snapshot: ControlCenterSnapshot }) {
  const { ci, deployments, observedAt, repository, rulesets } = snapshot.github;
  return (
    <article className="panel setup-panel" aria-label="Fresh GitHub project evidence">
      <div className="panel-heading compact">
        <div><span className="section-kicker">Live GitHub evidence</span><h2>{repository.state === "ready" ? `${repository.defaultBranch} · ${shortSha(repository.headSha)}` : integrationStateLabel(repository.state)}</h2></div>
        <StatusPill tone={integrationStateTone(repository.state)}>{repository.state === "ready" ? "Fresh" : integrationStateLabel(repository.state)}</StatusPill>
      </div>
      <div className="runtime-signals">
        <div><span>Latest CI</span><strong className={ci.run?.conclusion === "success" ? "positive" : ""}>{workflowRunLabel(snapshot)}</strong></div>
        <div><span>Deployments</span><strong>{deployments.state === "ready" ? deployments.items.length : integrationStateLabel(deployments.state)}</strong></div>
        <div><span>Rulesets</span><strong>{rulesets.state === "ready" ? rulesets.items.filter((item) => item.enforcement === "active").length : integrationStateLabel(rulesets.state)}</strong></div>
        <div><span>Observed</span><strong>{observedAt ? formatObservationTime(observedAt) : "No evidence"}</strong></div>
      </div>
      <div className="pr-insight"><span className="insight-icon"><Icon name="shield" size={18} /></span><p><strong>Read-only snapshot:</strong> a short-lived installation token fetched this evidence server-side. No token, private key, raw API response, or write action reaches the browser.</p></div>
    </article>
  );
}

export default function ControlCenter({ authorization, connection, previewMode = false, signOutPath, siteProvisioned, snapshot, user }: ControlCenterProps) {
  const [activeNav, setActiveNav] = useState("overview");
  const [commandOpen, setCommandOpen] = useState(false);
  const [expertOpen, setExpertOpen] = useState(false);
  const [panel, setPanel] = useState<PanelName>(null);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState("");
  const [wizardMode, setWizardMode] = useState<WizardMode>(connection.connectionMode === "tunnel" ? "tunnel" : "local");
  const commandRef = useRef<HTMLElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const openPanel = (nextPanel: PanelName, navName?: string) => {
    setPanel(nextPanel);
    setCommandOpen(false);
    setQuery("");
    if (navName) setActiveNav(navName);
  };

  const closeCommand = () => {
    setCommandOpen(false);
    setQuery("");
  };

  const commands: CommandItem[] = [
    { action: () => openPanel("pull-requests", "pull-requests"), detail: "View the explicit PR integration state", icon: "pr", label: "Open pull-request status" },
    { action: () => openPanel("grok-review", "grok-review"), detail: "View the explicit Grok review integration state", icon: "review", label: "Open Grok review results" },
    { action: () => openPanel("connection", "connection"), detail: "Choose local development or Secure MCP Tunnel", icon: "cloud", label: "Open connection wizard" },
    { action: () => openPanel("repository", "repository"), detail: "Configure repository metadata without a GitHub token", icon: "github", label: "Review repository setup" },
    { action: () => openPanel("review", "review"), detail: "Inspect control-surface privacy and secret boundaries", icon: "review", label: "View control guardrails" },
    { action: () => openPanel("runtime", "runtime"), detail: "Understand what the Site can and cannot verify", icon: "activity", label: "Review runtime boundary" },
    { action: () => openPanel("deployments", "deployments"), detail: "Review the canonical Site deployment gate", icon: "deploy", label: "Open deployment checklist" },
  ];
  const filteredCommands = commands.filter((command) =>
    `${command.label} ${command.detail}`.toLowerCase().includes(query.toLowerCase()),
  );

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((value) => !value);
      }
      if (event.key === "Escape") {
        setCommandOpen(false);
        setPanel(null);
        setQuery("");
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  useEffect(() => {
    if (commandOpen) window.setTimeout(() => searchRef.current?.focus(), 30);
  }, [commandOpen]);

  useEffect(() => {
    const container = commandOpen ? commandRef.current : panel ? drawerRef.current : null;
    if (!container) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const getFocusable = () => Array.from(
      container.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex='-1'])"),
    ).filter((element) => element.isConnected);
    if (!commandOpen) window.setTimeout(() => getFocusable()[0]?.focus(), 30);
    const trapFocus = (event: KeyboardEvent) => {
      const focusable = getFocusable();
      if (event.key !== "Tab" || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", trapFocus);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [commandOpen, panel]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const copyText = async (value: string, successMessage: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setToast(successMessage);
    } catch {
      setToast("Clipboard access is unavailable");
    }
  };

  const navItems: { id: string; label: string; icon: IconName; panel: PanelName }[] = [
    { id: "overview", label: "Overview", icon: "home", panel: null },
    { id: "pull-requests", label: "Pull requests", icon: "pr", panel: "pull-requests" },
    { id: "grok-review", label: "Grok review", icon: "review", panel: "grok-review" },
    { id: "connection", label: "Connection", icon: "cloud", panel: "connection" },
    { id: "runtime", label: "Runtime", icon: "activity", panel: "runtime" },
    { id: "deployments", label: "Deployment", icon: "deploy", panel: "deployments" },
  ];
  const initials = displayInitials(user.displayName);
  const liveGitHubAuthorization = authorization.source === "live-github-collaborator";
  const modeLabel = !connection.configured ? "Setup needed" : connection.connectionMode === "local" ? "Local development" : "Secure tunnel";
  const pullRequestMetric = snapshot.pullRequests.state === "ready" ? `${snapshot.pullRequests.items.length} open` : integrationStateLabel(snapshot.pullRequests.state);
  const grokReviewMetric = snapshot.grokReview.state === "ready" ? snapshot.grokReview.verdict ?? scoreLabel(snapshot.grokReview.score) : integrationStateLabel(snapshot.grokReview.state);

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand-block">
          <div className="brand-mark"><Icon name="spark" size={21} /></div>
          <div className="brand-copy"><strong>UniGrok</strong><span>Contributor Control</span></div>
        </div>
        <nav className="main-nav">
          {navItems.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${activeNav === item.id ? "active" : ""}`}
              onClick={() => {
                setActiveNav(item.id);
                setPanel(item.panel);
              }}
              aria-current={activeNav === item.id ? "page" : undefined}
              title={item.label}
            >
              <Icon name={item.icon} size={21} />
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-security">
          <Icon name="lock" size={18} />
          <div><strong>Two checks passed</strong><span>{liveGitHubAuthorization ? "GitHub identity + fresh role" : "ChatGPT + project role"}</span></div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          {connection.repositoryUrl ? (
            <a className="repo-select" href={connection.repositoryUrl} target="_blank" rel="noreferrer">
              <Icon name="github" size={20} /><span>{connection.repository}</span><Icon name="external" size={15} />
            </a>
          ) : (
            <button className="repo-select" onClick={() => openPanel("repository", "repository")}>
              <Icon name="github" size={20} /><span>Repository not configured</span><Icon name="chevron" size={15} />
            </button>
          )}
          <div className="branch-select" aria-label="Authorized GitHub role"><Icon name="branch" size={18} /><span>{authorization.role}</span></div>
          <div className="topbar-spacer" />
          <div className="sync-state"><span className={`sync-dot ${connection.configured ? "" : "pending"}`} /><span>{connection.configured ? "Setup selected" : "Setup required"}</span></div>
          <button className="avatar" onClick={() => openPanel("settings", "settings")} aria-label="Open identity and privacy settings">{initials}</button>
        </header>

        <div className="content-wrap">
          <section className="hero-section">
            <div className="hero-copy">
              <div className="eyebrow"><span className="eyebrow-dot" /> {liveGitHubAuthorization ? "GitHub identity + fresh collaborator authorization" : "ChatGPT identity + server-side project binding"}</div>
              <h1>{liveGitHubAuthorization ? "Your live project control snapshot." : connection.configured ? "Your control-center shell is configured." : "Connect your UniGrok control plane."}</h1>
              <p>{liveGitHubAuthorization ? "GitHub identity and repository permission were checked for this request. Installation credentials stay server-side and every repository field below is sanitized before display." : "This legacy Sites fallback uses a server-configured bootstrap binding. The canonical control origin performs live GitHub OAuth and collaborator verification; secrets stay outside the browser."}</p>
            </div>
            <button className="icon-button mobile-menu" onClick={() => setCommandOpen(true)} aria-label="Open command center"><Icon name="menu" /></button>
          </section>

          <button className="command-trigger" onClick={() => setCommandOpen(true)} aria-label="Open UniGrok command palette">
            <Icon name="search" size={22} /><span>Jump to setup, security, or deployment…</span><kbd><span>⌘</span>K</kbd>
          </button>

          <section className="metric-grid" aria-label="Control-center status summary">
            <button className="metric-card violet" onClick={() => openPanel("pull-requests", "pull-requests")}>
              <span className="metric-icon"><Icon name="pr" size={25} /></span>
              <span className="metric-copy"><small>Pull requests</small><strong className="metric-word">{pullRequestMetric}</strong><em>{snapshot.pullRequests.state === "ready" ? "Sanitized repository data" : "No approved data adapter"}</em></span><Icon name="chevron" size={17} />
            </button>
            <button className="metric-card cyan" onClick={() => openPanel("grok-review", "grok-review")}>
              <span className="metric-icon"><Icon name="review" size={25} /></span>
              <span className="metric-copy"><small>Grok review</small><strong className="metric-word">{grokReviewMetric}</strong><em>{snapshot.grokReview.state === "ready" ? scoreLabel(snapshot.grokReview.score) : "No review result claimed"}</em></span><Icon name="chevron" size={17} />
            </button>
            <button className={liveGitHubAuthorization ? "metric-card cyan" : connection.configured ? "metric-card cyan" : "metric-card amber"} onClick={() => openPanel(liveGitHubAuthorization ? "deployments" : "runtime", liveGitHubAuthorization ? "deployments" : "runtime")}>
              <span className="metric-icon"><Icon name={liveGitHubAuthorization ? "check" : "activity"} size={25} /></span>
              <span className="metric-copy"><small>{liveGitHubAuthorization ? "Latest CI" : "Runtime health"}</small><strong className="metric-word">{liveGitHubAuthorization ? workflowRunLabel(snapshot) : "Unverified"}</strong><em>{liveGitHubAuthorization ? workflowRunDetail(snapshot) : modeLabel}</em></span><Icon name="chevron" size={17} />
            </button>
            <button className="metric-card neutral" onClick={() => openPanel("settings", "settings")}>
              <span className="metric-icon"><Icon name="shield" size={25} /></span>
              <span className="metric-copy"><small>Access</small><strong className="metric-word">Authorized</strong><em>@{authorization.githubLogin} · {authorization.role}</em></span><Icon name="chevron" size={17} />
            </button>
          </section>

          {liveGitHubAuthorization && <GitHubEvidenceSurface snapshot={snapshot} />}

          <section className="dashboard-grid">
            <article className="panel pull-panel">
              <div className="panel-heading">
                <div><span className="section-kicker">Repository signal</span><h2>Pull-request status</h2></div>
                <button className="text-button" onClick={() => openPanel("pull-requests", "pull-requests")}>Details <Icon name="chevron" size={16} /></button>
              </div>
              <PullRequestSurface snapshot={snapshot} />
            </article>

            <div className="right-stack">
              <article className="panel review-panel">
                <div className="panel-heading compact">
                  <div><span className="section-kicker">UniGrok signal</span><h2>Grok review results</h2></div>
                  <button className="text-button" onClick={() => openPanel("grok-review", "grok-review")}>Details <Icon name="chevron" size={16} /></button>
                </div>
                <GrokReviewSurface snapshot={snapshot} />
              </article>

              <article className="panel runtime-panel">
                <div className="panel-heading compact">
                  <div><span className="section-kicker">Runtime health</span><h2>Unverified from this Site</h2></div>
                  <button className="text-button" onClick={() => openPanel("runtime", "runtime")}>Verify safely <Icon name="chevron" size={16} /></button>
                </div>
                <div className="runtime-state"><span className="runtime-icon"><Icon name="activity" size={22} /></span><div><strong>Check inside the UniGrok trust boundary</strong><span>Run a local health check or tunnel doctor; no synthetic result is displayed</span></div></div>
                <div className="runtime-signals">
                  <div><span>Health route</span><strong className="positive">/healthz</strong></div>
                  <div><span>MCP route</span><strong>/mcp</strong></div>
                  <div><span>Browser secrets</span><strong className="positive">None</strong></div>
                  <div><span>Hosted localhost</span><strong>Unsupported</strong></div>
                </div>
              </article>
            </div>
          </section>

          <article className="panel setup-panel compact-setup">
            <div className="panel-heading">
              <div><span className="section-kicker">Connection wizard</span><h2>Choose the boundary that matches your deployment</h2></div>
              <button className="text-button" onClick={() => openPanel("connection", "connection")}>Open wizard <Icon name="chevron" size={16} /></button>
            </div>
            <div className="connection-options">
              <button className={connection.connectionMode === "local" ? "connection-option selected" : "connection-option"} onClick={() => { setWizardMode("local"); openPanel("connection", "connection"); }}>
                <span className="connection-option-icon local"><Icon name="terminal" size={22} /></span>
                <span><small>Same device</small><strong>Local UniGrok</strong><p>For local development only. A hosted Site cannot reach this endpoint.</p></span>
                <StatusPill tone={connection.connectionMode === "local" ? "green" : "muted"}>{connection.connectionMode === "local" ? "Selected" : "Local only"}</StatusPill>
              </button>
              <button className={connection.connectionMode === "tunnel" ? "connection-option selected" : "connection-option"} onClick={() => { setWizardMode("tunnel"); openPanel("connection", "connection"); }}>
                <span className="connection-option-icon tunnel"><Icon name="cloud" size={22} /></span>
                <span><small>Hosted companion</small><strong>Secure MCP Tunnel</strong><p>Outbound-only tunnel configured in ChatGPT Plugins, with no inbound public listener.</p></span>
                <StatusPill tone={connection.connectionMode === "tunnel" ? "green" : "blue"}>{connection.connectionMode === "tunnel" ? "Selected" : "Hosted"}</StatusPill>
              </button>
            </div>
            <div className="pr-insight"><span className="insight-icon"><Icon name="shield" size={18} /></span><p><strong>Safe default:</strong> this Site never asks the browser for an xAI key, tunnel credential, GitHub token, or project identity.</p></div>
          </article>

          <section className="next-move">
            <div className="next-label"><span className="next-icon"><Icon name="spark" size={21} /></span><div><small>Your next move</small><strong>{connection.configured ? "Review the canonical deployment checklist" : "Choose a safe UniGrok connection mode"}</strong></div></div>
            <button className="primary-action" onClick={() => openPanel(connection.configured ? "deployments" : "connection", connection.configured ? "deployments" : "connection")}><span>{connection.configured ? "Deployment checklist" : "Open connection wizard"}</span><Icon name="arrow" size={19} /></button>
            <button className="secondary-action" onClick={() => openPanel("review", "review")}><Icon name="shield" size={18} /><span>Review guardrails</span></button>
            <a className="link-action" href={sitesGuide} target="_blank" rel="noreferrer">Sites documentation <Icon name="external" size={16} /></a>
          </section>

          <section className="engineering-section">
            <button className="engineering-toggle" onClick={() => setExpertOpen((value) => !value)} aria-expanded={expertOpen}>
              <span><Icon name="terminal" size={19} /><span><strong>Advanced engineering context</strong><small>Identity, runtime, transport, and secret boundaries</small></span></span>
              <span className={expertOpen ? "chevron-open" : ""}><Icon name="chevron" size={18} /></span>
            </button>
            {expertOpen && (
              <div className="engineering-grid">
                <div><span>Authentication</span><strong>{liveGitHubAuthorization ? "GitHub App OAuth + PKCE" : "Dispatch-owned SIWC"}</strong><small>{liveGitHubAuthorization ? "The browser retains only a signed HttpOnly session." : "Viewer identity arrives through trusted Sites request headers."}</small></div>
                <div><span>Authorization</span><strong>@{authorization.githubLogin} · {authorization.role}</strong><small>{liveGitHubAuthorization ? "Fresh collaborator permission checked for this request." : "Legacy fallback binding; canonical control uses live GitHub verification."}</small></div>
                <div><span>Health contract</span><strong><code>GET /healthz</code></strong><small>Run from the UniGrok host or tunnel-client trust boundary.</small></div>
                <div><span>MCP transport</span><strong><code>POST /mcp</code></strong><small>Streamable HTTP; no browser credential is collected here.</small></div>
                <div><span>Site identity</span><strong>Canonical project</strong><small>The repository is bound to the existing UniGrok Site.</small></div>
              </div>
            )}
          </section>
        </div>
      </section>

      {commandOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && closeCommand()}>
          <section ref={commandRef} className="command-modal" role="dialog" aria-modal="true" aria-labelledby="command-title">
            <h2 id="command-title" className="sr-only">UniGrok command palette</h2>
            <div className="command-search"><Icon name="search" size={22} /><input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search setup and security actions" aria-label="Search commands" /><button onClick={closeCommand} aria-label="Close command palette"><Icon name="x" size={18} /></button></div>
            <div className="command-results"><span className="command-label">Jump to</span>{filteredCommands.length > 0 ? filteredCommands.map((command) => <button key={command.label} onClick={command.action}><span className="command-item-icon"><Icon name={command.icon} size={19} /></span><span><strong>{command.label}</strong><small>{command.detail}</small></span><Icon name="arrow" size={17} /></button>) : <div className="empty-command">No matching control-center action</div>}</div>
            <div className="command-footer"><span><kbd>⌘K</kbd> Toggle</span><span><kbd>esc</kbd> Close</span></div>
          </section>
        </div>
      )}

      {panel && (
        <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setPanel(null)}>
          <aside ref={drawerRef} className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
            <button className="drawer-close" onClick={() => setPanel(null)} aria-label="Close detail panel"><Icon name="x" size={19} /></button>
            {panel === "connection" && <ConnectionWizard connection={connection} copyText={copyText} mode={wizardMode} setMode={setWizardMode} />}
            {panel === "pull-requests" && <PullRequestDetail connection={connection} snapshot={snapshot} />}
            {panel === "repository" && <RepositoryDetail connection={connection} />}
            {panel === "grok-review" && <GrokReviewDetail snapshot={snapshot} />}
            {panel === "review" && <ReviewDetail />}
            {panel === "runtime" && <RuntimeDetail connection={connection} />}
            {panel === "deployments" && <DeploymentDetail siteProvisioned={siteProvisioned} snapshot={snapshot} />}
            {panel === "settings" && <SettingsDetail authorization={authorization} displayName={user.displayName} previewMode={previewMode} signOutPath={signOutPath} />}
          </aside>
        </div>
      )}

      {toast && <div className="toast" role="status"><Icon name="check" size={17} />{toast}</div>}
    </main>
  );
}

function DrawerHeader({ description, eyebrow, icon, title }: { description: string; eyebrow: string; icon: IconName; title: string }) {
  return <header className="drawer-header"><span className="drawer-icon"><Icon name={icon} size={23} /></span><span className="drawer-eyebrow">{eyebrow}</span><h2 id="drawer-title">{title}</h2><p>{description}</p></header>;
}

function ConnectionWizard({ connection, copyText, mode, setMode }: { connection: PublicConnectionConfig; copyText: (value: string, successMessage: string) => Promise<void>; mode: WizardMode; setMode: (mode: WizardMode) => void }) {
  const profile = connection.tunnelProfile ?? "unigrok";
  const localBaseUrl = connection.localBaseUrl ?? "http://127.0.0.1:4765";
  const localConfiguration = `UNIGROK_CONNECTION_MODE=local\nUNIGROK_LOCAL_BASE_URL=${localBaseUrl}`;
  const tunnelConfiguration = `UNIGROK_CONNECTION_MODE=tunnel\nUNIGROK_TUNNEL_PROFILE=${profile}`;
  const localTabRef = useRef<HTMLButtonElement>(null);
  const tunnelTabRef = useRef<HTMLButtonElement>(null);
  const selectMode = (nextMode: WizardMode, focus = false) => {
    setMode(nextMode);
    if (focus) window.requestAnimationFrame(() => (nextMode === "local" ? localTabRef : tunnelTabRef).current?.focus());
  };
  const handleTabKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    let nextMode: WizardMode | null = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") nextMode = mode === "local" ? "tunnel" : "local";
    if (event.key === "Home") nextMode = "local";
    if (event.key === "End") nextMode = "tunnel";
    if (!nextMode) return;
    event.preventDefault();
    selectMode(nextMode, true);
  };
  return (
    <div className="drawer-content">
      <DrawerHeader icon="cloud" eyebrow="Safe connection wizard" title="Connect without leaking the trust boundary" description="Choose the path that matches where UniGrok runs. This wizard never accepts or stores credentials." />
      <div className="wizard-tabs" role="tablist" aria-label="Connection mode">
        <button ref={localTabRef} id="connection-tab-local" className={mode === "local" ? "active" : ""} onClick={() => selectMode("local")} onKeyDown={handleTabKey} role="tab" aria-controls="connection-panel-local" aria-selected={mode === "local"} tabIndex={mode === "local" ? 0 : -1}><Icon name="terminal" size={17} /> Local UniGrok</button>
        <button ref={tunnelTabRef} id="connection-tab-tunnel" className={mode === "tunnel" ? "active" : ""} onClick={() => selectMode("tunnel")} onKeyDown={handleTabKey} role="tab" aria-controls="connection-panel-tunnel" aria-selected={mode === "tunnel"} tabIndex={mode === "tunnel" ? 0 : -1}><Icon name="cloud" size={17} /> Secure MCP Tunnel</button>
      </div>
      {mode === "local" ? (
        <div id="connection-panel-local" role="tabpanel" aria-labelledby="connection-tab-local" tabIndex={0}>
          <div className="runtime-callout warning"><Icon name="shield" size={22} /><p><strong>Local means this device only</strong><span>A deployed Site cannot reach your laptop through localhost. Use this mode only while running the Site locally beside UniGrok.</span></p></div>
          <section className="drawer-section steps-list"><h3>Local verification</h3><p><span>1</span><span className="step-copy">Start UniGrok on the loopback interface.</span></p><p><span>2</span><span className="step-copy">Run <code>curl -fsS {localBaseUrl}/healthz</code> on the UniGrok machine.</span></p><p><span>3</span><span className="step-copy">Use <code>{localBaseUrl}/mcp</code> only in a local MCP client.</span></p></section>
          <section className="drawer-section"><h3>Local environment metadata</h3><pre className="safe-code"><code>{localConfiguration}</code></pre><button className="secondary-action drawer-wide-action" onClick={() => copyText(localConfiguration, "Local configuration copied")}><Icon name="code" size={17} /> Copy local configuration</button></section>
        </div>
      ) : (
        <div id="connection-panel-tunnel" role="tabpanel" aria-labelledby="connection-tab-tunnel" tabIndex={0}>
          <div className="runtime-callout"><Icon name="shield" size={22} /><p><strong>No inbound public listener</strong><span>The tunnel client runs beside UniGrok and makes an outbound connection to OpenAI. The Site does not receive the tunnel credential.</span></p></div>
          <section className="drawer-section steps-list"><h3>Secure tunnel checklist</h3><p><span>1</span><span className="step-copy">Create the tunnel in your own OpenAI Platform tunnel settings.</span></p><p><span>2</span><span className="step-copy">Run <code>tunnel-client</code> in the same trust boundary as UniGrok.</span></p><p><span>3</span><span className="step-copy">Validate with <code>tunnel-client doctor --profile {profile} --explain</code>.</span></p><p><span>4</span><span className="step-copy">In ChatGPT Settings → Plugins, choose the Tunnel connection.</span></p></section>
          <section className="drawer-section"><h3>Non-secret Site metadata</h3><pre className="safe-code"><code>{tunnelConfiguration}</code></pre><button className="secondary-action drawer-wide-action" onClick={() => copyText(tunnelConfiguration, "Tunnel metadata copied")}><Icon name="code" size={17} /> Copy tunnel metadata</button></section>
          <a className="external-doc-link" href={secureTunnelGuide} target="_blank" rel="noreferrer">Read the Secure MCP Tunnel guide <Icon name="external" size={16} /></a>
        </div>
      )}
    </div>
  );
}

function PullRequestDetail({ connection, snapshot }: { connection: PublicConnectionConfig; snapshot: ControlCenterSnapshot }) {
  return (
    <div className="drawer-content">
      <DrawerHeader icon="pr" eyebrow="Pull-request status" title={integrationStateLabel(snapshot.pullRequests.state)} description="Review state and release impact are separate. Changes requested affects that PR only unless an approved adapter explicitly marks it as a release blocker." />
      <section className="drawer-section embedded-surface"><PullRequestSurface snapshot={snapshot} /></section>
      <section className="drawer-section data-list"><h3>Adapter boundary</h3><div><span>Repository</span><strong>{connection.repository ?? "Not configured"}</strong></div><div><span>Source state</span><strong>{integrationStateLabel(snapshot.pullRequests.state)}</strong></div><div><span>GitHub token in browser</span><strong className="green-text">None</strong></div></section>
    </div>
  );
}

function GrokReviewDetail({ snapshot }: { snapshot: ControlCenterSnapshot }) {
  return (
    <div className="drawer-content">
      <DrawerHeader icon="review" eyebrow="Grok review results" title={integrationStateLabel(snapshot.grokReview.state)} description="No score, verdict, or finding is invented. Results appear only after an approved, server-side UniGrok review adapter is connected." />
      <section className="drawer-section embedded-surface"><GrokReviewSurface snapshot={snapshot} /></section>
      <div className="runtime-callout"><Icon name="shield" size={21} /><p><strong>Sanitized result contract</strong><span>An adapter must allowlist fields, cap strings and findings, and render all result text through React without raw HTML.</span></p></div>
    </div>
  );
}

function RepositoryDetail({ connection }: { connection: PublicConnectionConfig }) {
  return (
    <div className="drawer-content">
      <DrawerHeader icon="github" eyebrow="Repository metadata" title={connection.repository ?? "Repository not configured"} description="Repository links are public metadata. In standalone control mode, authenticated GitHub requests use a short-lived installation token exclusively on the server." />
      <section className="drawer-section data-list"><h3>Configuration</h3><div><span>Environment key</span><strong><code>GITHUB_REPOSITORY</code></strong></div><div><span>Expected shape</span><strong><code>owner/repository</code></strong></div><div><span>GitHub token in browser</span><strong className="green-text">None</strong></div><div><span>Current state</span><strong>{connection.repository ? "Configured" : "Missing"}</strong></div></section>
      <div className="runtime-callout"><Icon name="shield" size={21} /><p><strong>Keep GitHub authorization separate</strong><span>Add repository integrations only through a reviewed, server-side authorization flow. Never paste a personal access token into this Site.</span></p></div>
      {connection.repositoryUrl && <a className="external-doc-link" href={connection.repositoryUrl} target="_blank" rel="noreferrer">Open configured repository <Icon name="external" size={16} /></a>}
    </div>
  );
}

function ReviewDetail() {
  return (
    <div className="drawer-content">
      <DrawerHeader icon="review" eyebrow="Control safeguards" title="Review required before landing" description="These are product requirements, not a review receipt. Codex must inspect the complete diff before changes are landed or published." />
      <section className="drawer-section security-list">{guardrails.map((item) => <div key={item}><span className="security-check"><Icon name="check" size={16} /></span><p><strong>{item}</strong><small>Required by the repository security contract.</small></p></div>)}</section>
      <div className="runtime-callout"><Icon name="lock" size={21} /><p><strong>Review before commit or publish</strong><span>Inspect the complete diff, run the deployment safety scan, and verify the bound hosting manifest before creating a deployment.</span></p></div>
    </div>
  );
}

function RuntimeDetail({ connection }: { connection: PublicConnectionConfig }) {
  return (
    <div className="drawer-content">
      <DrawerHeader icon="activity" eyebrow="Runtime boundary" title="Verification is deliberately local" description="The Site reports configuration metadata only. It does not proxy arbitrary URLs or use a visitor-supplied target." />
      <section className="drawer-section data-list"><h3>Configured metadata</h3><div><span>Mode</span><strong>{connection.connectionMode}</strong></div><div><span>Label</span><strong>{connection.endpointLabel}</strong></div><div><span>Health route</span><strong><code>/healthz</code></strong></div><div><span>MCP route</span><strong><code>/mcp</code></strong></div></section>
      <section className="drawer-section steps-list"><h3>Truthful verification</h3><p><span>1</span><span className="step-copy">For local mode, run the health command on the UniGrok machine.</span></p><p><span>2</span><span className="step-copy">For tunnel mode, run tunnel-client doctor beside UniGrok.</span></p><p><span>3</span><span className="step-copy">Treat this Site as configuration guidance until an approved integration is added.</span></p></section>
    </div>
  );
}

function DeploymentDetail({ siteProvisioned, snapshot }: { siteProvisioned: boolean; snapshot: ControlCenterSnapshot }) {
  const liveEvidence = snapshot.github.repository.state !== "unconfigured";
  return (
    <div className="drawer-content">
      <DrawerHeader icon="deploy" eyebrow={liveEvidence ? "Read-only GitHub evidence" : "Canonical Site deployment"} title={liveEvidence ? `${snapshot.github.deployments.items.length} recent deployment records` : siteProvisioned ? "Review this Site’s deployment gate" : "Site identity missing"} description={liveEvidence ? snapshot.github.deployments.message : siteProvisioned ? "The canonical project identity is present. Complete verification and save a version only after review." : "The canonical project identity is required before this source can be deployed."} />
      {liveEvidence && <section className="drawer-section data-list"><h3>Repository evidence</h3><div><span>Latest CI</span><strong>{workflowRunLabel(snapshot)}</strong></div><div><span>Default branch</span><strong>{snapshot.github.repository.defaultBranch ?? "Unavailable"}</strong></div><div><span>Head SHA</span><strong><code>{shortSha(snapshot.github.repository.headSha)}</code></strong></div><div><span>Active rulesets</span><strong>{snapshot.github.rulesets.items.filter((item) => item.enforcement === "active").length}</strong></div><div><span>Installation token in browser</span><strong className="green-text">None</strong></div></section>}
      <section className="pipeline"><div className="complete"><span><Icon name="check" size={16} /></span><p><strong>Review the repository changes</strong><small>Keep credentials out of source and browser state</small></p></div><div className={siteProvisioned ? "complete" : "blocked"}><span>{siteProvisioned ? <Icon name="check" size={16} /> : <Icon name="clock" size={16} />}</span><p><strong>{siteProvisioned ? "Canonical Site identity present" : "Restore the canonical Site binding"}</strong><small>{siteProvisioned ? "Deployment is bound to the existing UniGrok project" : "Do not deploy an unbound checkout"}</small></p></div><div><span>3</span><p><strong>Configure hosted authorization bindings</strong><small>Missing or malformed bindings deny control access</small></p></div><div><span>4</span><p><strong>Preview and request Codex review</strong><small>Do not deploy until the review is clean</small></p></div><div><span>5</span><p><strong>Approve a production deployment</strong><small>Verify public and protected routes separately</small></p></div></section>
      <a className="external-doc-link" href={sitesGuide} target="_blank" rel="noreferrer">Open ChatGPT Sites documentation <Icon name="external" size={16} /></a>
    </div>
  );
}

function SettingsDetail({ authorization, displayName, previewMode, signOutPath }: { authorization: Extract<GitHubProjectAuthorization, { authorized: true }>; displayName: string; previewMode: boolean; signOutPath: string }) {
  const liveGitHubAuthorization = authorization.source === "live-github-collaborator";
  return (
    <div className="drawer-content">
      <DrawerHeader icon="shield" eyebrow="Identity and authorization" title="Two independent checks passed" description={liveGitHubAuthorization ? "GitHub OAuth established identity. A fresh installation-token permission lookup separately confirmed a contributor role for this repository." : "This legacy Sites fallback uses dispatcher identity plus a bootstrap binding. The canonical control origin uses GitHub OAuth and a fresh collaborator lookup."} />
      <section className="drawer-section data-list"><h3>Current request</h3><div><span>{liveGitHubAuthorization ? "GitHub display" : "ChatGPT display"}</span><strong>{displayName}</strong></div><div><span>GitHub identity</span><strong>@{authorization.githubLogin}</strong></div><div><span>Project role</span><strong>{authorization.role}</strong></div><div><span>Browser credential storage</span><strong className="green-text">Signed session only</strong></div></section>
      <div className="runtime-callout"><Icon name="shield" size={21} /><p><strong>Privacy disclosure</strong><span>{liveGitHubAuthorization ? "The GitHub user token is used only during the callback to establish identity and is then discarded. Installation tokens and App credentials remain server-side. The browser retains a signed, HttpOnly identity session without GitHub credentials." : "Sites supplies the authenticated email and may supply a full name to the server for each request. Email is compared to the server-held project-role bindings and is never sent to the browser. Only a normalized full name or generic label reaches this browser. Neither value is retained or sent to UniGrok."}</span></p></div>
      {liveGitHubAuthorization ? <form action={signOutPath} method="post"><button className="secondary-action drawer-wide-action" type="submit">Sign out with GitHub</button></form> : <a className="secondary-action drawer-wide-action" href={signOutPath}>{previewMode ? "Exit local preview" : "Sign out with ChatGPT"}</a>}
    </div>
  );
}

function displayInitials(value: string): string {
  const parts = value.split(/\s+/).filter(Boolean).slice(0, 2);
  const initials = parts.map((part) => part[0]).join("").toUpperCase();
  return initials || "U";
}

function boundedInteger(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

function integrationStateLabel(state: IntegrationState): string {
  return state === "ready" ? "Ready" : state === "loading" ? "Loading" : state === "error" ? "Unavailable" : "Not connected";
}

function integrationStateTone(state: IntegrationState): "green" | "amber" | "blue" | "muted" {
  return state === "ready" ? "green" : state === "loading" ? "blue" : state === "error" ? "amber" : "muted";
}

function reviewStateLabel(state: "approved" | "changes_requested" | "pending"): string {
  return state === "approved" ? "Approved" : state === "changes_requested" ? "Changes" : "Pending";
}

function reviewStateTone(state: "approved" | "changes_requested" | "pending"): "green" | "amber" | "muted" {
  return state === "approved" ? "green" : state === "changes_requested" ? "amber" : "muted";
}

function safePullRequestUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" ? url.toString() : null;
  } catch {
    return null;
  }
}

function scoreLabel(score: number | null): string {
  return score === null ? "No score" : `${Math.min(100, Math.max(0, boundedInteger(score)))}/100`;
}

function workflowRunLabel(snapshot: ControlCenterSnapshot): string {
  const { run, state } = snapshot.github.ci;
  if (state !== "ready") return integrationStateLabel(state);
  if (!run) return "No runs";
  return run.status === "completed" ? run.conclusion ?? "Completed" : run.status.replaceAll("_", " ");
}

function workflowRunDetail(snapshot: ControlCenterSnapshot): string {
  const run = snapshot.github.ci.run;
  return run ? `${run.name} · ${shortSha(run.headSha)}` : snapshot.github.ci.message;
}

function shortSha(value: string | null): string {
  return value && /^[0-9a-f]{40}$/i.test(value) ? value.slice(0, 8) : "Unavailable";
}

function formatObservationTime(value: string): string {
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? date.toISOString().replace("T", " ").slice(0, 16) + " UTC" : "Unavailable";
}
