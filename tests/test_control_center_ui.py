"""Control Center honesty and pane-contract regression tests.

Pipeline plan: docs/design/ui-data-pipeline.md. The dashboard must render
runtime truth (readyz status verbatim), expose the plane/kind aggregates, and
carry the Time/Kind/Stop receipt columns.
"""

import asyncio
from pathlib import Path

from unigrok_public import server
from unigrok_public.state import PublicStateStore

DASHBOARD = Path(server.__file__).parent / "static" / "dashboard.html"


def test_service_pill_never_hardcodes_ready() -> None:
    # The old page unconditionally claimed "Service ready". The pill must
    # render readyz.status verbatim instead; the literal claim may not return.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "'Service ready'" not in html
    assert '"Service ready"' not in html
    assert "ready.status" in html


def test_dashboard_carries_new_panes_and_receipt_columns() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    for pane_id in ('id="planes"', 'id="kinds"', 'id="runtime"', 'id="routing"', 'id="tools"'):
        assert pane_id in html
    for column in ("<th>Time</th>", "<th>Kind</th>", "<th>Stop</th>"):
        assert column in html
    assert "UI_BUILD" in html


def test_tables_replaced_by_groupby_and_standouts() -> None:
    # Tools and receipts lead with a group-by chart card; only ranked standouts
    # (severity math) drop to a compact table, full detail behind a <details>.
    html = DASHBOARD.read_text(encoding="utf-8")
    for pane_id in (
        'id="toolbill"',
        'id="outcomes"',
        'id="risktools"',
        'id="standouts"',
        'id="claimstate"',
    ):
        assert pane_id in html
    assert "function cbars(" in html
    assert "const rsev=" in html  # per-receipt severity scoring
    assert html.count("<details") >= 2  # full tool + receipt lists collapsed


def test_per_panel_color_coding_and_legend() -> None:
    # Each dimension panel gets a meaningful hue (planes/kinds/routes/models),
    # metric tiles threshold-color, and a level legend decodes the palette.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "function pbars(" in html
    for fn in ("PLANE_COL", "KIND_COL", "ROUTE_COL", "MODEL_COL", "METERED_KINDS"):
        assert fn in html
    assert 'class="legend"' in html
    assert "lv-great" in html and "lv-threat" in html


def test_connect_panel_and_plane_usage() -> None:
    # Forge fold: an MCP-connect panel (non-secret config, live client status)
    # and per-plane usage reporting on the routing planes.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="clients"' in html and 'id="mcpsnippet"' in html
    assert "mcpConfig" in html and "X-Client-ID" in html
    assert "planeUse" in html
    # config must stay non-secret: no API key field wired into the snippet
    assert "XAI_API_KEY" not in html and "api_key" not in html


def test_governance_and_build_panels() -> None:
    # Smart adds from real /runtimez data: build/durable metrics and the
    # policy/governance flags (spend-enabling reads warning).
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="build"' in html and 'id="policy"' in html
    assert "grok_build" in html and "api_spend_enforcement" in html
    assert "routing_advisor" in html and "automatic_judge_spend" in html


def test_contributor_sample_panels_gated() -> None:
    # Sky/Space contributor shells (reviews, live run, report card, devices)
    # exist, are badged SAMPLE, and live inside the tier-gated sections.
    html = DASHBOARD.read_text(encoding="utf-8")
    for pane_id in ('id="ghreviews"', 'id="liverun"', 'id="reportcard"', 'id="devices"'):
        assert pane_id in html
    # sealed report card must not headline a fabricated non-floor claim
    assert "sealed" in html.lower() and "% floor" in html


def test_tier_nav_renders_all_three_surfaces() -> None:
    # Sponsor decision: the unified switcher shows all three tiers and links
    # each to its own surface port (public 4765, sky 4768, space 4769). Higher
    # tiers enforce their own auth on arrival; the nav is navigation, not access.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="tiernav"' in html
    for token in (
        "@grok Public Core",
        "@skygrok Sky Observer",
        "@spacegrok Space Awareness",
        "'4765'",
        "'4768'",
        "'4769'",
    ):
        assert token in html


def test_tier_scoped_panels_present_and_gated() -> None:
    # Sky/Space panels exist but are display:none by default; JS reveals them
    # only when the active tier warrants (hierarchical scoping). Sample data is
    # clearly marked and no sealed READY / non-null gate_id is invented.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="sky-tier"' in html and 'id="space-tier"' in html
    assert html.count('style="display:none"') >= 2
    for panel in ("4-lane swarm grid", "Claim plane", "security monitor"):
        assert panel.lower() in html.lower()
    assert "SAMPLE" in html
    assert "gate_id null" in html.lower() or "gate_id" in html
    assert "sealed READY" not in html or "no sealed READY" in html


def test_level_color_palette_wired() -> None:
    # The six-level canvas palette (dark hexes) and a levelOf() classifier drive
    # status-text color across service/plane/breaker/claim-plane surfaces.
    html = DASHBOARD.read_text(encoding="utf-8")
    for hexval in ("#3fa266", "#81a1c1", "#7bafe9", "#f1b467", "#dd7f76", "#fc6b83"):
        assert hexval in html.lower()
    for cls in (".lv-great", ".lv-good", ".lv-expected", ".lv-threat", ".lv-critical"):
        assert cls in html
    assert "function levelOf(" in html


def test_runtimez_serves_public_tool_registry() -> None:
    from unigrok_public.server import PUBLIC_TOOLS, _runtime_public_tools

    tools = _runtime_public_tools()
    assert len(tools) == len(PUBLIC_TOOLS)
    sample = tools[0]
    for field in ("name", "plane", "purpose", "billing_class", "destructive"):
        assert field in sample


def test_dashboard_keeps_single_inline_script_for_nonce() -> None:
    # _ui_index_response injects the CSP nonce into the first <script> only;
    # a second inline script would ship without a nonce and be blocked.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert html.count("<script") == 1


def test_telemetry_summary_exposes_kind_and_plane_aggregates(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "state.db")
    for kind, plane in (("agent", "api"), ("web_search", "cli"), ("agent", "api")):
        asyncio.run(
            store.save_telemetry(
                {
                    "caller": "dev-seed:test",
                    "request_kind": kind,
                    "route": "agent",
                    "resolved_plane": plane,
                    "model": "grok-4",
                    "verified": True,
                    "success": True,
                    "latency_ms": 100,
                }
            )
        )
    summary = asyncio.run(store.telemetry_summary())
    kinds = {bucket["name"]: bucket["calls"] for bucket in summary["kinds"]}
    planes = {bucket["name"]: bucket["calls"] for bucket in summary["planes"]}
    assert kinds == {"agent": 2, "web_search": 1}
    assert planes == {"api": 2, "cli": 1}
    recent = summary["recent"][0]
    for field in ("created_at", "request_kind", "stop_reason"):
        assert field in recent
