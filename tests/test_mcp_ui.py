from starlette.testclient import TestClient

from src.http_server import create_app


def test_mcp_ui_static_files_are_served(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")

    assert index.status_code == 200
    assert "<title>UniGrok MCP v0.5.3 Control Center</title>" in index.text
    assert '<span class="version-badge">v0.5.3</span>' in index.text
    assert 'script type="module" src="./app.js?v=grok-v0.5.3"' in index.text
    assert '<link rel="stylesheet" href="./styles.css?v=grok-v0.5.3" />' in index.text
    assert "Control Center" in index.text
    assert "Bearer token" not in index.text
    assert "Quick Test Console" in index.text
    assert script.status_code == 200
    assert "tools/call" in script.text
    assert "X-Client-ID" in script.text
    assert "/runtimez" in script.text
    assert "grok_mcp_discover_self" in script.text
    assert "simulate_reasoning_guard" in script.text
    assert "fetch_okf_bundle" in script.text
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
    assert "Never paste XAI_API_KEY into this page" in index.text
    assert "Optional organization API comparison" in index.text
    assert "renderRoutingReceipts" in script.text
    assert 'fetchMcpCall("grok_mcp_discover_self", { include_models: true })' in script.text
    assert "renderPlaneModelCatalog" in script.text
    assert "CLI subscription" in script.text
    assert "API metered" in script.text
    assert "list.replaceChildren()" in script.text
    assert "routing?.why_detail" in script.text
    assert styles.status_code == 200
    assert ".console-grid" in styles.text
    assert ".metric-card" in styles.text
    assert ".routing-receipt" in styles.text
    assert ".model-plane-grid" in styles.text
    assert ".provider-model-card" in styles.text


def test_mcp_ui_loads_without_exposing_mcp_when_auth_is_active(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
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


def test_mcp_ui_browser_warning():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="browserWarningCard"' in index.text
    assert "Browser compatibility" in index.text


def test_mcp_ui_token_drift_wizard():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="apiKeyWizard"' in index.text
    assert 'id="wizardTokenInput"' in index.text
    assert 'id="saveWizardTokenBtn"' in index.text


def test_mcp_ui_token_storage_is_in_memory_only():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")
    assert script.status_code == 200
    assert 'localStorage.setItem("unigrok.clientToken"' not in script.text
    assert 'localStorage.getItem("unigrok.clientToken"' not in script.text


def test_mcp_ui_cost_estimator():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert 'id="costEstimator"' in index.text
    assert 'id="budgetGuardToggle"' in index.text
    assert "Local input estimate" in index.text
    assert "Estimated Cost" not in index.text


def test_mcp_ui_accessibility_audit():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
        styles = client.get("/ui/styles.css")
    assert index.status_code == 200
    assert 'aria-label="Prompt task message input"' in index.text
    assert 'aria-label="Client token"' in index.text
    assert styles.status_code == 200
    assert "prefers-reduced-motion" in styles.text


def test_mcp_ui_large_okf_fallback():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        script = client.get("/ui/app.js")
    assert script.status_code == 200
    assert "Warning: Large file loaded" in script.text
    assert "50000" in script.text  # 50KB limit check


def test_mcp_ui_security_csp_headers():
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        index = client.get("/ui/")
    assert index.status_code == 200
    assert "content-security-policy" in index.headers
    csp = index.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
