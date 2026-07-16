import json
from html.parser import HTMLParser
from pathlib import Path

from starlette.testclient import TestClient

from src.http_server import create_app


ROOT = Path(__file__).resolve().parents[1]


class _AnchorHrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.hrefs_by_id: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if href is None:
            return
        self.hrefs.append(href)
        anchor_id = values.get("id")
        if anchor_id is not None:
            self.hrefs_by_id[anchor_id] = href


def _parse_anchor_hrefs(markup: str) -> _AnchorHrefParser:
    parser = _AnchorHrefParser()
    parser.feed(markup)
    return parser


def test_mcp_ui_static_files_are_served(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")

    assert index.status_code == 200
    assert "<title>UniGrok Gateway Console v0.6.0</title>" in index.text
    assert '<span class="version-badge">v0.6.0</span>' in index.text
    assert 'script type="module" src="./app.js?v=grok-v0.6.0-r14"' in index.text
    assert '<link rel="stylesheet" href="./styles.css?v=grok-v0.6.0-r14" />' in index.text
    assert '<link rel="stylesheet" href="./tokens.css?v=grok-v0.6.0-r14" />' in index.text
    assert "Console" in index.text
    assert 'id="surfaceModeBadge"' in index.text
    assert 'id="tab-btn-schemas"' not in index.text
    assert 'id="tab-btn-okf"' not in index.text
    assert 'id="tab-btn-webmcp"' not in index.text
    assert 'id="tab-btn-guard"' not in index.text
    assert 'id="nav-link-swarm"' not in index.text
    assert "product-law" in index.text
    assert 'id="tab-btn-onboarding"' in index.text
    assert 'id="metricVerifiedSplit"' in index.text
    assert 'id="nav-group-contributor"' not in index.text
    assert 'nav-link-swarm' not in index.text
    assert 'Swarm Optimizer' not in index.text
    assert 'product-law' in index.text
    assert "Bearer token" not in index.text
    assert 'id="tab-btn-console"' not in index.text
    assert 'id="tab-console"' not in index.text
    assert 'id="verifySetupBtn"' not in index.text
    assert 'id="runSampleBtn"' not in index.text
    assert "Chat lives in your IDE" in index.text
    assert "Legacy Mode" not in index.text
    init_body = script.text.split("function init()", 1)[1]
    assert "setupConsoleActions();" not in init_body
    assert "switchTab(\"tab-onboarding\")" in script.text or "switchTab('tab-onboarding')" in script.text
    assert script.status_code == 200
    assert "tools/call" in script.text
    assert "X-Client-ID" in script.text
    assert "/runtimez" in script.text
    assert "grok_mcp_discover_self" in script.text
    assert "simulate_reasoning_guard" in script.text
    assert "get_result_shape_example" in script.text
    assert 'name: "get_schema"' in script.text
    assert "Deprecated compatibility alias" in script.text
    assert "authoritative: false" in script.text
    assert "Live MCP tools/list schemas are authoritative" in script.text
    assert "Health" in index.text
    assert 'id="readinessHero"' in index.text
    assert 'id="cliPlaneCard"' in index.text
    assert 'id="apiPlaneCard"' in index.text
    assert 'id="spendGlanceCard"' in index.text
    assert 'id="mcpEndpointDisplay"' in index.text
    assert 'id="copyMcpJsonBtn"' in index.text
    assert 'id="copyAgentPromptBtn"' in index.text
    assert 'aria-label="Copy Cursor MCP JSON"' in index.text
    assert 'aria-label="Copy agent setup prompt"' in index.text
    assert 'id="copyPrimaryActionBtn"' in index.text
    assert 'id="agentPromptSnippet"' in index.text
    assert 'id="discoverSelfDetails"' in index.text
    assert "resolveMcpEndpoint" in script.text
    assert "renderPlaneCards" in script.text
    assert "renderSpendGlance" in script.text
    assert "genericMcpJson" in script.text
    assert "agentSetupPrompt" in script.text
    assert "syncPrimaryCta" in script.text
    assert "resetConversation();" not in script.text
    assert 'detailText ? "Gateway needs attention." : "Gateway offline."' in script.text
    assert "api_cost_usd ??" in script.text
    assert "details.open = true" in script.text
    assert 'textContent?.replace(/^tools:' in script.text or "textContent?.replace(/^tools:" in script.text
    assert ".readiness-hero" in styles.text
    assert ".glass-grid" in styles.text
    assert ".plane-card" in styles.text
    assert ".connect-panel" in styles.text
    assert ".connect-snippet-block" in styles.text
    assert "overflow-y: auto" in styles.text
    assert "startup ingestion is not automatic" in index.text
    assert "before hitting the API" not in script.text
    assert "safely route this call" not in script.text
    assert '"grok-4.3": 0' in script.text
    assert '"grok-4.5": 0' in script.text
    assert "no declared reasoning_effort" in script.text
    assert "Standard / Medium" not in index.text
    assert "Premier / High" not in index.text
    assert 'auto: { prompt: "Describe quantum computing."' in script.text
    profiles = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".grok" / "hyperparams").glob("*.json")
    ]
    assert profiles
    assert all("reasoning_effort" not in profile for profile in profiles)
    assert "fetch_okf_bundle" in script.text
    assert 'fetch("/docs/okf/okf-manifest.json")' in script.text
    assert "source.files.map" in script.text
    assert 'fetchMcpCall("grok_mcp_status", { view: "json" })' in script.text
    assert "Naive regex parse" not in script.text
    assert 'id="metricApiCost"' in index.text
    assert 'id="planeBreakdownBody"' in index.text
    assert 'id="providerUsageState"' in index.text
    assert 'id="cliUsageState"' in index.text
    assert "SuperGrok CLI subscription" in index.text
    assert 'id="routingReceipts"' in index.text
    assert 'id="routeClassBreakdown"' in index.text
    assert 'id="selectionReasonBreakdown"' in index.text
    assert 'id="callerBreakdownTitle"' in index.text
    assert "caller_attributed_requests" in script.text
    assert 'class="raw-telemetry-disclosure"' in index.text
    assert "period.callers || payload.callers" in script.text
    assert ".raw-telemetry-disclosure pre" in styles.text
    assert 'id="factSelection"' in index.text
    assert 'id="credentialAlert"' in index.text
    assert 'id="planeChip"' in index.text
    assert 'data-tab="tab-models"' in index.text
    assert 'id="cliModelPlane"' in index.text
    assert 'id="apiModelPlane"' in index.text
    assert 'id="sharedModelsNote"' in index.text
    assert "Models &amp; Credential Planes" in index.text
    assert 'id="copyCredentialActionBtn"' in index.text
    assert "renderCredentialPlanes" in script.text
    assert "actionableCredentialNotice" in script.text
    assert 'contract?.effective_plane === "none"' in script.text
    assert "Never put XAI_API_KEY in IDE MCP settings" in index.text
    assert "Optional organization API comparison" in index.text
    assert "renderRoutingReceipts" in script.text
    assert 'fetchMcpCall("grok_mcp_discover_self", { include_models: true })' in script.text
    assert "renderPlaneModelCatalog" in script.text
    assert "CLI subscription" in script.text
    assert "API metered" in script.text
    assert "list.replaceChildren()" in script.text
    assert "planePinSnippet" in script.text
    assert "fallback_policy=same_plane" in script.text
    assert "isFallbackCatalogSource" in script.text
    assert "renderPlaneRepair" in script.text
    assert "clearPlaneModelLists" in script.text
    assert "loadPlaneModelCatalog(false)" in script.text
    # Operational unavailable sources must not be styled as static fallback catalogs.
    fallback_set = script.text.split("FALLBACK_CATALOG_SOURCES")[1].split("];")[0]
    assert '"cloudrun-disabled"' not in fallback_set
    assert '"skipped"' not in fallback_set
    # Plane selector must warm dual-plane catalog, not only /v1/models.
    assert "if (!state.modelCatalog && !state.modelCatalogLoading)" in script.text
    # Credential rechecks must invalidate credential-dependent catalog snapshots.
    assert "credentialPlaneCatalogSignature" in script.text
    assert "priorSignature !== nextSignature" in script.text
    assert "state.modelCatalog = null" in script.text
    assert "state.modelCatalogGeneration += 1" in script.text
    assert 'state.activeTab === "tab-models"' in script.text
    assert 'clearModelOptions("auto route")' in script.text
    assert "generation !== state.modelCatalogGeneration" in script.text
    legacy_loader = script.text.split("async function loadModelsList()", 1)[1].split(
        "function syncModelOptions()", 1
    )[0]
    assert '$("modelInput")' not in legacy_loader
    assert 'headers: { "X-Client-ID": "cursor" }' in script.text
    assert "Cursor is the first-class host IDE" in script.text
    assert 'id="cliCatalogTrust"' in index.text
    assert 'id="apiCatalogTrust"' in index.text
    assert 'id="cliPlaneRepair"' in index.text
    assert 'id="apiPlaneRepair"' in index.text
    assert "Cursor is first-class" in index.text
    assert "catalog-trust-banner" in styles.text
    assert ".plane-repair" in styles.text
    assert "routing?.why_detail" in script.text
    assert styles.status_code == 200
    assert ".console-grid" in styles.text
    assert ".metric-card" in styles.text
    assert ".routing-receipt" in styles.text
    assert ".model-plane-grid" in styles.text
    assert ".provider-model-card" in styles.text
    assert 'id="navSplitter"' in index.text
    assert 'id="inspectorSplitter"' in index.text
    assert 'data-region="workbench"' in index.text
    assert 'role="tablist"' in index.text
    assert 'role="separator"' in index.text
    assert "ResizeObserver" in script.text
    assert "resolveLayout" in script.text
    assert "unigrok_ui_layout_get" in script.text
    assert "mcp.console.layout.v2" in script.text
    assert "grid-template-columns: 1fr !important" not in styles.text
    assert "fonts.googleapis.com" not in index.text
    assert 'id="planeInput"' not in index.text
    assert 'id="fallbackPolicyInput"' not in index.text
    assert 'id="factBilling"' in index.text


def test_mcp_ui_swarm_playground_is_served_and_honest(monkeypatch):
    """The swarm Pareto Playground: served statically, consumes the
    unigrok-swarm-status-v2 payload plus v1 exports (the local/public
    symmetry), and keeps the no-simulated-data stance in its own copy."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        page = client.get("/ui/swarm.html")
        script = client.get("/ui/swarm.js")

    assert page.status_code == 200
    assert "Pareto Playground" in page.text
    assert "Nothing is simulated" in page.text
    assert script.status_code == 200
    assert "unigrok-swarm-status-v1" in script.text
    assert "unigrok-swarm-status-v2" in script.text
    # Swarm remains statically served for deep links, but Core Console nav
    # no longer promotes it (health-glass IA; IDE MCP is primary chat).
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert 'id="nav-link-swarm"' not in index.text
    assert 'href="./swarm.html"' not in index.text
    assert "Swarm Optimizer" not in index.text
    assert "get_swarm_status" in script.text
    assert 'id="fileBtn"' in page.text
    assert "onclick=" not in page.text  # blocked by the server's script-src CSP
    assert 'state.source !== "live"' in script.text
    assert "apply is disabled for static exports" in script.text
    # On-ramp: sample run, recent-swarm picker, one-click golden demo — a
    # human can test the instrument without pasting a task id.
    assert 'id="sampleBtn"' in page.text
    assert 'id="demoBtn"' in page.text
    assert 'id="taskPicker"' in page.text
    assert 'id="refreshTasksBtn"' in page.text
    assert 'id="runtimeBanner"' in page.text
    assert 'id="sourceBadge"' in page.text
    assert 'id="tradeoffSummary"' in page.text
    assert "Already loaded" in page.text
    assert "Advanced: task ID" in page.text
    assert "list_swarm_tasks" in script.text
    assert "./swarm-sample.json" in script.text
    assert "nsquared_dedup" in script.text  # the golden demo target
    assert 'fetch("/runtimez"' in script.text
    assert "Stable gateway connected" in script.text
    # Single-origin: the hardcoded cross-port :4766 Forge link is retired. Forge
    # is referenced only as a visible-but-locked note, never a live link.
    assert "4766" not in script.text
    assert "4766" not in page.text
    assert 'id="forgeNote"' in page.text
    # The manual bearer-token field is gone (same-origin session).
    assert 'id="token"' not in page.text
    assert 'type="password"' not in page.text
    assert '$("token")' not in script.text
    # Run flow consumes a structured task_id, else polls list_swarm_tasks — it
    # never scrapes a task id out of prose with a backtick regex.
    assert "parseStructuredTaskId" in script.text
    assert "resolveTaskId" in script.text
    assert "match(/`" not in script.text
    # Shared fluid system: the bespoke inline stylesheet is deleted; the page
    # links the shared styles.css and reuses the shared .panel-splitter.
    assert "<style>" not in page.text
    assert 'href="./styles.css' in page.text
    assert "await loadSample()" in script.text  # useful content, not empty boxes, on arrival
    assert "rpc.result?.isError" in script.text
    assert 'c.setAttribute("tabindex", "0")' in script.text
    assert "leadingFrontId" in script.text
    assert "no new candidates" in script.text
    assert "Math.max(0, lo - span * 0.08)" in script.text
    assert "Bandit selection receipt" in script.text
    # Zero-user v2 on-ramp: a large paste surface gives deterministic metrics
    # before any model call. Stable/cloud mode stays browser-only.
    assert 'id="codeInput"' in page.text
    assert 'maxlength="262144"' in page.text
    assert 'id="analyzeBtn"' in page.text
    assert 'id="analysisResults"' in page.text
    assert "analyze_code_for_swarm" in script.text
    assert 'state.runtimeMode === "contributor"' in script.text
    assert "source was not uploaded" in script.text
    # Phase 2 progressive disclosure: scoring is opt-in and its oracle boxes are
    # auto-scaffolded from ONE sample-inputs field, labeled honestly. The tool
    # still receives test_code + bench_code — the UI writes them.
    assert "Score it against your tests" in page.text
    assert 'id="sampleInput"' in page.text
    assert "Sample inputs (JSON)" in page.text
    assert "We scaffolded this" in page.text
    assert "strengthen it for real correctness" in page.text
    assert "scaffoldOracles" in script.text
    assert "SWARM_BENCH" in script.text  # the scaffolded bench emits the contract token
    # The missing_tests/missing_benchmark markers are reframed as optional-to-
    # verify and feature-detected across the current and softened server shapes.
    assert "classifySearchability" in script.text
    assert "Optional to verify" in script.text
    # Inline pre-launch validation surfaces problems at the field, not post-burn.
    assert "validateOraclesBeforeRun" in script.text
    # The bundled sample is a REAL recorded run and says so inside itself.
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        sample = client.get("/ui/swarm-sample.json")
    assert sample.status_code == 200
    payload = sample.json()
    assert payload["format"] == "unigrok-swarm-status-v1"
    assert "scripted for reproducibility" in payload["provenance"]
    assert "/Users/" not in sample.text
    assert ".claude/worktrees/" not in sample.text
    assert payload["pareto_front"]  # non-empty front to explore
    assert payload["aggregates"]["best_latency_improvement_pct"] > 0
    # Walls are never plotted at invented coordinates.
    assert "gutter" in script.text
    # Apply stays gated by mode in the UI exactly like the tool.
    assert "apply is disabled outside UNIGROK_SWARM=active" in script.text
    assert "apply is disabled while the swarm is still running" in script.text


def test_mcp_ui_swarm_verification_is_opt_in_and_scaffolded(monkeypatch):
    """The disabled-until-two-empty-boxes verification gate is gone. Free
    analyze is code-only; scoring is an opt-in toggle whose oracle boxes are
    auto-scaffolded from ONE sample-inputs field. start_paste_swarm still
    receives test_code + bench_code — the UI generates them, not the user."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        page = client.get("/ui/swarm.html")
        script = client.get("/ui/swarm.js")

    assert page.status_code == 200
    assert script.status_code == 200

    # ── The old gate is gone ────────────────────────────────────────────────
    # The Run button no longer starts hard-disabled in the markup, and the two
    # "required" empty oracle textareas are no longer demanded up front.
    assert 'id="runPasteBtn" class="primary" type="button" disabled' not in page.text
    assert "pytest code · required" not in page.text
    assert "benchmark script · required" not in page.text
    assert "Run verified local search: add tests and benchmark" not in page.text
    assert "Print one SWARM_BENCH JSON line" not in page.text

    # ── The opt-in / scaffold affordance is present ─────────────────────────
    assert "Score it against your tests" in page.text  # the opt-in summary
    assert 'id="sampleInput"' in page.text            # the one honest input
    assert "Sample inputs (JSON)" in page.text
    assert 'id="rescaffoldBtn"' in page.text
    # Both oracles still exist (the tool requires them) but are scaffolded and
    # labeled honestly rather than hand-authored into two empty boxes.
    assert 'id="testInput"' in page.text
    assert 'id="benchInput"' in page.text
    assert "baseline-equivalence pytest" in page.text
    assert "SWARM_BENCH harness" in page.text
    assert "We scaffolded this" in page.text
    assert "strengthen it for real correctness" in page.text

    # ── The scaffolds are generated client-side and honest ──────────────────
    assert "scaffoldOracles" in script.text
    assert "scaffoldBenchHarness" in script.text
    assert "scaffoldEquivalenceTest" in script.text
    assert "topHotspot" in script.text  # focus auto-picks the hottest function
    assert "SWARM_BENCH" in script.text  # the bench scaffold prints the contract token
    assert "module_under_test" in script.text  # the equivalence test imports the candidate

    # ── missing_tests / missing_benchmark are optional-to-verify, not red ────
    # Feature-detected across the current (blockers) and softened (advisory)
    # server shapes — never hard-depended on one.
    assert "classifySearchability" in script.text
    assert "SCAFFOLDABLE_MARKERS" in script.text
    assert "advisory" in script.text
    assert "Optional to verify" in script.text

    # ── Inline pre-launch validation surfaces problems at the field ─────────
    assert "validateOraclesBeforeRun" in script.text
    assert "must print a SWARM_BENCH contract line" in script.text
    assert "must import from module_under_test" in script.text

    # ── The tool still receives both UI-generated code blocks ───────────────
    assert "start_paste_swarm" in script.text
    assert "test_code:" in script.text
    assert "bench_code:" in script.text

    # ── CSP stays intact: no inline handlers, no JS eval, no CDN ────────────
    assert "onclick=" not in page.text
    assert " eval(" not in script.text
    assert "<style>" not in page.text


def test_mcp_ui_layout_engine_is_local_and_ide_first():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")

    assert 'data-nav="dock"' in index.text
    assert 'data-inspector="hidden"' in index.text
    assert 'aria-label="Workspace layout controls"' in index.text
    assert "--workbench-min: 300px" in styles.text
    assert '.console-grid[data-nav="rail"]' in styles.text
    assert '.console-grid[data-inspector="hidden"]' in styles.text
    assert '.console-grid[data-inspector="drawer"]' in styles.text
    assert 'data-inspector-drawer="closed"' in index.text
    assert 'class="form-actions playground-action-dock"' not in index.text
    assert "Health" in index.text
    assert 'id="advancedNav"' not in index.text
    assert "product-law" in index.text
    assert 'inspectorPresence: "hide"' in script.text
    assert "PANEL_MODES" not in script.text
    assert "cyclePanel" not in script.text
    assert "pointerdown" in script.text
    assert 'event.key.toLowerCase() === "b"' in script.text
    resize_observer_body = script.text.split("new ResizeObserver", 1)[1].split("observer.observe", 1)[0]
    assert "fetch(" not in resize_observer_body
    assert "fetchMcpCall" not in resize_observer_body


def test_mcp_ui_loads_without_exposing_mcp_when_auth_is_active(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv(
        "UNIGROK_API_KEY_RECORDS", '{"ui-client":"client-secret"}'
    )
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)

    with TestClient(
        create_app(),
        base_url="http://localhost:8080",
        client=("127.0.0.1", 50000),
    ) as client:
        index = client.get("/ui/")
        denied = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert index.status_code == 200
    assert denied.status_code == 401


# --- v0.4.1 Proactive Robustness Tests ---

def test_mcp_ui_docker_health_restart_button():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="dockerOfflineAlert"' in index.text
    assert 'id="dockerRestartBtn"' in index.text
    assert 'id="copyManualRestartBtn"' in index.text
    assert 'id="restartManualFallback"' in index.text
    assert 'id="offlineAlertMessage"' in index.text


def test_mcp_ui_file_preview_routes_to_live_control_center():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")

    assert 'window.location.protocol === "file:"' in script.text
    assert 'const LIVE_UI_URL = "http://localhost:4765/ui/"' in script.text
    assert 'restartBtn.dataset.action = "open-live-ui"' in script.text
    assert 'style.setProperty("display", "none", "important")' in script.text
    assert "window.location.assign(LIVE_UI_URL)" in script.text
    assert "window.location.replace(LIVE_UI_URL)" in script.text
    assert "setTimeout(renderFilePreviewNotice, 700)" in script.text
    assert "pollReadyz();" in script.text

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        styles = client.get("/ui/styles.css")
    assert ".alert-banner.preview-banner" in styles.text
    assert "position: fixed" in styles.text
    assert ".preview-banner #restartManualFallback" in styles.text


def test_mcp_ui_browser_warning():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="browserWarningCard"' in index.text
    assert "Browser note" in index.text


def test_mcp_ui_has_no_browser_credential_wizard():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="apiKeyWizard"' not in index.text
    assert 'id="wizardTokenInput"' not in index.text
    assert 'id="saveWizardTokenBtn"' not in index.text


def test_mcp_ui_token_storage_is_in_memory_only():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")
    assert script.status_code == 200
    assert 'localStorage.setItem("unigrok.clientToken"' not in script.text
    assert 'localStorage.getItem("unigrok.clientToken"' not in script.text


def test_mcp_ui_has_no_browser_inference_cost_estimator():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="costEstimator"' not in index.text
    assert 'id="budgetGuardToggle"' not in index.text
    assert "Local input estimate" not in index.text
    assert "Estimated Cost" not in index.text


def test_mcp_ui_accessibility_audit():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        styles = client.get("/ui/styles.css")
    assert index.status_code == 200
    assert 'aria-label="Console sections"' in index.text
    assert 'aria-label="Gateway status"' in index.text
    assert 'aria-label="Prompt task message input"' not in index.text
    assert 'aria-label="Client token"' not in index.text
    assert styles.status_code == 200
    assert "prefers-reduced-motion" in styles.text


def test_mcp_ui_large_okf_fallback():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")
    assert script.status_code == 200
    assert "Warning: Large file loaded" in script.text
    assert "50000" in script.text  # 50KB limit check


def test_mcp_ui_markdown_renderer_is_shared_and_escape_first():
    """One escape-first renderer (markdown.js) serves both the OKF viewer and
    the agent transcript; the old fence-mangling inline renderer is gone."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")
        renderer = client.get("/ui/markdown.js")
    assert renderer.status_code == 200
    assert "export function parseMarkdown" in renderer.text
    assert "export function sanitizeHref" in renderer.text
    # Escape-first stays the architecture: entities before any fixed tag.
    assert '.replace(/&/g, "&amp;")' in renderer.text
    # Fences are extracted before inline passes, so ``` blocks survive.
    assert "FENCE_TOKEN" in renderer.text
    # Links go through the allowlist (load-bearing regex + routing), not a
    # comment: pin the allowlist source and that linkTag calls sanitizeHref.
    assert "^(https?:" in renderer.text
    assert "sanitizeHref(url)" in renderer.text
    # C0 control chars are stripped from source so they cannot hide a scheme.
    assert "\\u000E-\\u001F" in renderer.text
    # app.js imports the shared renderer at the current cache-bust version and
    # no longer defines its own.
    assert 'from "./markdown.js?v=grok-v0.6.0-r14"' in script.text
    assert "import { parseMarkdown" in script.text
    assert "function parseMarkdown" not in script.text
    assert "renderMarkdownInto" in script.text
    # Agent bubbles render markdown; user/system/error bubbles stay plain.
    assert 'sender === "agent"' in script.text


def test_mcp_ui_error_surfaces_tell_the_truth():
    """Failed tool runs must never show a green SUCCESS receipt, chat must not
    swallow JSON-RPC errors, and a 503 /readyz names its failing checks."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")
    assert "TOOL ERROR" in script.text
    assert "response.result?.isError" in script.text
    assert "Gateway rejected the call" in script.text
    assert 'addMessageBubble("error"' in script.text
    assert ".msg-error" in styles.text
    assert 'id="factFinishReason"' in index.text
    assert 'id="factDegraded"' in index.text
    assert "payload.finish_reason" in script.text
    assert "extractToolPayload(response) || {}" in script.text
    # AgentResult finish_reason=error is a normal tools/call payload — must not
    # paint SUCCESS, and the transcript must use an error bubble.
    assert 'payload.finish_reason === "error"' in script.text
    assert 'statusLabel = "FAILED"' in script.text
    assert 'statusLabel = "DEGRADED"' in script.text
    assert "failing checks" in script.text
    assert "describeNotReady" in script.text
    # The connection-lost wording is reserved for actual fetch failures.
    assert "No connection detected" in script.text


def test_mcp_ui_fact_cost_tells_the_truth_for_zero():
    """cost_usd=0 must not render as '-' (unknown); subscription $0 says so."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")
    assert 'typeof rawBilling === "string" ? rawBilling.trim() : ""' in script.text
    assert 'billing.toLowerCase() === "subscription"' in script.text
    assert "cost === 0 && isSubscription" in script.text
    assert '"Subscription"' in script.text
    assert "cost.toFixed(5)" in script.text


def test_mcp_ui_swarm_messages_tell_the_truth():
    """The paste-swarm flow only claims success on completed runs, the privacy
    line never denies an attempted upload, and valid Python without top-level
    defs is not misreported as a parse error."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/swarm.js")
    assert "source was not uploaded" in script.text  # still true browser-only
    assert "the paste was submitted to the local gateway" in script.text
    assert 'final.status === "completed"' in script.text
    assert "Swarm ended with status" in script.text
    assert "Run did not complete" in script.text
    assert "!result.parse_ok && result.parse_error" in script.text
    assert "approximate browser scanner" in script.text


def test_mcp_ui_github_review_widget_is_marked_and_honest():
    """The PR-review widget names itself as the live MCP resource template and
    surfaces the server's degraded flag instead of always looking green."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        page = client.get("/ui/github-review-v1.html")
    assert page.status_code == 200
    assert "ui://widget/unigrok-github-review-v1.html" in page.text
    assert "<title>UniGrok PR Review Widget</title>" in page.text
    assert "degraded route" in page.text
    assert "data.degraded" in page.text


def test_mcp_ui_retains_receipt_inspection_without_browser_chat():
    """Historical agent receipts remain inspectable, while the Core UI no
    longer exposes a prompt, workspace-context, or browser-session surface."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
    # Only the agent call writes the routing receipt.
    assert 'if (toolName === "agent")' in script.text
    assert "renderCitations" in script.text
    # Mode provenance rows.
    for fid in ("factRequestedMode", "factModeSource", "factDialedPort"):
        assert f'id="{fid}"' in index.text
    assert "payload.requested_mode" in script.text
    assert 'id="workspaceContextInput"' not in index.text
    assert 'id="sessionInput"' not in index.text
    assert 'id="promptInput"' not in index.text
    assert "setupConsoleActions();" not in script.text.split("function init()", 1)[1]


def test_mcp_ui_accessibility_and_dead_code_cleanup():
    """Status remains accessible after browser-chat controls are removed."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")
    assert 'id="dockerOfflineAlert" class="alert-banner hidden" role="alert"' in index.text
    assert 'role="tablist" aria-label="Console sections"' in index.text
    assert 'id="conversation"' not in index.text
    assert 'id="wizardTokenInput"' not in index.text
    # OKF list items are buttons, not click-only divs.
    assert 'document.createElement("button")' in script.text
    # Dead code is gone.
    assert "updateActiveContext" not in script.text
    assert "STORAGE_KEY" not in script.text
    assert "legacyModeToggle" not in script.text
    assert ".inspector-rail-btn" not in styles.text
    assert ".toggle-label-legacy" not in styles.text
    # The formerly-dead Prober Bridge button is now wired.
    assert 'id="runWebMcpBridgeBtn"' in index.text
    assert '$("runWebMcpBridgeBtn")?.addEventListener' in script.text


def test_mcp_ui_security_csp_headers():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert "content-security-policy" in index.headers
    csp = index.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_mcp_ui_assets_are_never_heuristically_cached():
    """Regression contract for the stale-skew incident: StaticFiles emits
    ETag/Last-Modified but no Cache-Control, so browsers heuristically cached
    index.html and paired it with a newer app.js — renderFactsPane then threw
    on missing receipt ids and a paid agent answer was discarded. no-cache
    keeps 304 revalidation cheap while forcing HTML and JS to stay in step."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
        tokens = client.get("/ui/tokens.css")
        okf = client.get("/docs/okf/okf-manifest.json")
        health = client.get("/healthz")

    for response in (index, script, tokens, okf):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
    # Non-static routes keep their own cache policies.
    assert health.headers.get("cache-control") != "no-cache"


def test_mcp_ui_asset_version_is_single_sourced():
    """Every copy of the cache-bust token must agree: src/version.py, the
    index.html meta/link/script pins, the markdown.js import inside app.js,
    app.js handshake constant, and the Swarm stylesheet/script/sample chain.
    A skewed pair is exactly the failure the version contract exists to catch."""
    from src.version import UI_ASSET_VERSION

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/").text
        script = client.get("/ui/app.js").text
        swarm = client.get("/ui/swarm.html").text
        swarm_script = client.get("/ui/swarm.js").text
        runtime = client.get("/runtimez").json()

    assert f'<meta name="unigrok-ui-version" content="{UI_ASSET_VERSION}" />' in index
    assert f'href="./tokens.css?v={UI_ASSET_VERSION}"' in index
    assert f'href="./styles.css?v={UI_ASSET_VERSION}"' in index
    assert f'src="./app.js?v={UI_ASSET_VERSION}"' in index
    assert f'const UI_ASSET_VERSION = "{UI_ASSET_VERSION}"' in script
    assert f'from "./markdown.js?v={UI_ASSET_VERSION}"' in script
    assert f'href="./tokens.css?v={UI_ASSET_VERSION}"' in swarm
    # The swarm page now also links the shared styles.css (its bespoke inline
    # stylesheet was retired); that pin must move in lockstep too.
    assert f'href="./styles.css?v={UI_ASSET_VERSION}"' in swarm
    assert f'src="./swarm.js?v={UI_ASSET_VERSION}"' in swarm
    assert f'const UI_ASSET_VERSION = "{UI_ASSET_VERSION}"' in swarm_script
    assert (
        'fetch(`./swarm-sample.json?v=${encodeURIComponent(UI_ASSET_VERSION)}`)'
        in swarm_script
    )
    assert runtime["ui_asset_version"] == UI_ASSET_VERSION
    # The handshake self-heals a stale cached page with one reload, then warns.
    assert "enforceUiVersionHandshake" in script
    assert "unigrok-ui-version" in script


def test_mcp_ui_receipt_pane_cannot_discard_answers():
    """The facts pane is diagnostics: rendering it is wrapped so a DOM error
    there can never re-throw into callAgent and replace an already-received
    agent answer with 'Invocation failed'. All pane writes are null-safe."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js").text

    assert "function setText(" in script
    assert "Receipt pane render failed" in script
    # The pane renders inside its own try/catch at the call site.
    call_site = script.split('if (toolName === "agent")', 1)[1].split("return responsePayload", 1)[0]
    assert "try" in call_site and "catch" in call_site
    # No unguarded direct innerText writes remain inside renderFactsPane.
    pane_body = script.split("function renderFactsPane", 1)[1].split("\n}\n", 1)[0]
    assert '$("fact' not in pane_body.replace('setText("fact', "")


def test_mcp_ui_not_ready_shows_one_onboarding_action():
    """A missing/unready gateway must produce a copyable quick-start, not a
    dead end. The card carries the exact README bootstrap commands and no
    environment-variable homework."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/").text
        script = client.get("/ui/app.js").text

    assert 'id="quickStartCard"' in index
    assert "git clone https://github.com/djtelicloud/grok-mcp-server.git" in index
    assert "docker compose up --build -d" in index
    assert 'id="copyQuickStartBtn"' in index
    # Shown only when the gateway is unreachable; a reachable gateway failing
    # a readiness check names the failing check instead of suggesting a
    # from-scratch reinstall.
    assert 'classList.toggle("hidden", Boolean(ready) || Boolean(detailText))' in script


def test_mcp_ui_local_surfaces_link_the_project_site():
    """Navigation is one product: both local pages link grokmcp.org, and the
    swarm page links back to the Control Center."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/").text
        swarm = client.get("/ui/swarm.html").text

    index_anchors = _parse_anchor_hrefs(index)
    swarm_anchors = _parse_anchor_hrefs(swarm)
    assert index_anchors.hrefs_by_id["nav-link-site"] == "https://grokmcp.org"
    assert swarm_anchors.hrefs[:2] == ["./index.html", "https://grokmcp.org"]


def test_mcp_ui_layout_limits_come_from_css():
    """Panel min/max bounds are authored once in styles.css custom properties;
    app.js reads them instead of duplicating pixel numbers."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js").text
        styles = client.get("/ui/styles.css").text

    assert "--nav-min: 148px" in styles
    assert "--inspector-max: 460px" in styles
    assert 'cssPx("--nav-min", 148)' in script
    assert 'cssPx("--inspector-max", 460)' in script


def test_mcp_ui_reflow_is_container_driven():
    """Pane content reflows on the workbench container's own width, not the
    viewport: a narrow workbench beside a wide inspector must still stack, so
    the old viewport media queries for those grids are gone."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        styles = client.get("/ui/styles.css").text

    assert "@container workbench (max-width: 1080px)" in styles
    assert "@container workbench (max-width: 760px)" in styles
    assert "@media (max-width: 1300px)" not in styles
    assert "@media (max-width: 1050px)" not in styles
    assert "@media (max-width: 768px)" not in styles
    # Fixed sidebar columns became user-resizable with clamps.
    assert "resize: horizontal" in styles


def test_mcp_ui_swarm_reflow_is_container_driven():
    """The swarm page reflows on its OWN container widths (swarm-shell /
    swarm-main), not the viewport: the brittle single @media(max-width:900px)
    plus the fixed 380px detail sidebar and vw/vh pane sizing are retired in
    favor of container queries, a keyboard-accessible resizable .panel-splitter
    detail pane, and a resize:horizontal editor clamped by cqw."""
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        styles = client.get("/ui/styles.css").text
        page = client.get("/ui/swarm.html").text
        script = client.get("/ui/swarm.js").text

    # Swarm reflow is authored as container queries on the swarm contexts.
    assert "container: swarm-shell / inline-size" in styles
    assert "container: swarm-main / inline-size" in styles
    # The shell collapse is driven by the ancestor swarm-app container — an
    # element cannot size-query the container it declares itself.
    assert "@container swarm-app (max-width: 900px)" in styles
    assert "@container swarm-main (max-width: 640px)" in styles
    # The old brittle viewport reflow is gone from the whole sheet.
    assert "@media (max-width: 900px)" not in styles
    # The detail pane is a real, keyboard-accessible splitter (shared class),
    # and the code editor is user-resizable — both like the Control Center.
    assert ".panel-splitter" in styles
    assert "resize: horizontal" in styles
    assert 'id="detailSplitter"' in page
    assert 'class="panel-splitter"' in page
    assert 'role="separator"' in page
    assert "setupDetailSplitter" in script
    assert "pointerdown" in script
    # The bespoke inline stylesheet (with its re-aliased tokens and fixed
    # 380px sidebar) is deleted, not just overridden.
    assert "<style>" not in page
    assert "380px" not in page


def test_ui_root_redirects_to_trailing_slash(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        response = client.get("/ui", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers.get("location", "").endswith("/ui/")
