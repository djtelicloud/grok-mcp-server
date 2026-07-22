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


def test_public_tier_nav_links_only_its_own_surface() -> None:
    # The public page renders its own tier and must never link or name the
    # higher-trust surfaces (anti-fingerprinting: no forge/sky/space hints).
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="tiernav"' in html
    for leak in ("4766", "4768", "4769", "skygrok", "spacegrok", "forge"):
        assert leak not in html.lower()


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
