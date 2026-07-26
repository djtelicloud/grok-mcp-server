"""Public dashboard honesty and pane-contract regression tests."""

import asyncio
import re
from pathlib import Path

from unigrok_public import server
from unigrok_public.state import PublicStateStore

DASHBOARD = Path(server.__file__).parent / "static" / "dashboard.html"


def test_service_pill_never_hardcodes_ready() -> None:
    # The pill text must be interpolated from readyz.status, not any fixed
    # string. Pin the template fragment so re-hardcoding it fails the test.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "'Service ready'" not in html
    assert '"Service ready"' not in html
    assert "`Service ${ready.status" in html


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
    # The public dashboard carries an MCP-connect panel (non-secret config,
    # live client status) and per-plane usage reporting.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="clients"' in html and 'id="mcpsnippet"' in html
    assert "planeUse" in html
    # Extract the mcpConfig definition and assert its shape positively: only a
    # url and a single X-Client-ID header — no credential field of any kind.
    m = re.search(r"const mcpConfig=.*?;", html)
    assert m, "mcpConfig definition not found"
    cfg = m.group(0)
    assert "url:" in cfg and "'X-Client-ID'" in cfg
    for secret in ("Authorization", "Bearer", "apiKey", "api_key", "XAI_API_KEY", "token"):
        assert secret not in cfg


def test_delegated_listeners_are_leak_free() -> None:
    # Exactly three listeners, all delegated on persistent roots (clients copy,
    # document click for the drawer, document keydown for Esc) so the 10 s
    # re-render can't stack listeners; no inline onclick anywhere.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "$('connectcard').addEventListener('click'" in html
    assert "document.addEventListener('click'" in html
    assert "document.addEventListener('keydown'" in html
    assert html.count("addEventListener") == 3
    assert "data-client" in html and "onclick=" not in html


def test_agent_paste_command_present_and_non_secret() -> None:
    # The connect panel carries both blocks: MCP JSON config and the remembered
    # agent paste command (claude mcp add for claude-code, an agent-readable
    # instruction otherwise). Neither may carry a credential.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="agentcmd"' in html and 'data-copy="agentcmd"' in html
    assert "claude mcp add --transport http unigrok" in html
    assert "grok_mcp_discover_self" in html
    m = re.search(r"const agentCmd=.*?;", html, re.S)
    assert m, "agentCmd definition not found"
    for secret in ("Authorization", "Bearer", "apiKey", "api_key", "XAI_API_KEY", "token"):
        assert secret not in m.group(0)


def test_command_drawer_indexes_only_public_panels() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="drawer"' in html and 'id="dmenu"' in html and 'id="backdrop"' in html
    for group in ("'This machine'", "'Build'"):
        assert group in html
    for removed_group in ("'Your project'", "'Account'"):
        assert removed_group not in html
    assert "scrollIntoView" in html


def test_severity_ranking_weights_and_slice() -> None:
    # rsev's weights, descending sort, and top-8 cap are the contract behind the
    # "needing attention" panel; pin them so a silent reorder/uncap fails.
    html = DASHBOARD.read_text(encoding="utf-8")
    for frag in (
        "s+=100",
        "s+=40",
        "Math.min(40,",
        "Math.min(30,",
        ".sort((a,b)=>b.s-a.s)",
        ".slice(0,8)",
    ):
        assert frag in html
    # cost must not flag every metered call — only notably expensive ones
    assert "c>=0.01" in html


def test_null_feed_never_fabricates_safe_state() -> None:
    # When a feed is unreachable the board shows "unavailable"/"—", never a
    # fabricated safe default (metered off / no destructive tools / sqlite).
    html = DASHBOARD.read_text(encoding="utf-8")
    build_guard = "if(!rt){$('build').innerHTML="
    assert build_guard in html and "runtime unavailable" in html
    assert "if(!rt){$('policy').innerHTML=" in html
    assert "!rt?'<tr><td colspan=\"3\" class=\"empty\">Registry unavailable.</td></tr>'" in html
    assert "rt?.state_backend||'sqlite'" not in html
    assert "Service unreachable" in html
    # independent per-feed catches so one failure can't sink the others
    assert html.count(".catch(()=>null)") >= 3


def test_governance_and_build_panels() -> None:
    # Smart adds from real /runtimez data: build/durable metrics and the
    # policy/governance flags (spend-enabling reads warning).
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="build"' in html and 'id="policy"' in html
    assert "grok_build" in html and "api_spend_enforcement" in html
    assert "routing_advisor" in html and "automatic_judge_spend" in html


def test_severity_scoring_handles_numeric_success() -> None:
    # SQLite serializes success as 0/1 ints; a strict ===false comparison would
    # silently drop every failed receipt from the standout ranking.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "r.success===false" not in html
    assert "r.verified&&!r.success" in html


def test_dashboard_is_public_core_only() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "UniGrok Core" in html
    for forbidden in (
        "@skygrok",
        "@spacegrok",
        "GroundCommand",
        "SkyCommand",
        "SpaceCommand",
        ":4768",
        ":4769",
        "UNIGROK_GITHUB_CLIENT_ID",
        "/auth/github",
        "/auth/control",
        "/api/me",
        "tier_nav",
        "?preview=",
        "SPACE=DARK",
        "4-lane swarm grid",
    ):
        assert forbidden not in html


def test_level_color_palette_wired() -> None:
    # The six-level canvas palette (dark hexes) and a levelOf() classifier drive
    # status-text color across service/plane/breaker/claim-plane surfaces.
    html = DASHBOARD.read_text(encoding="utf-8")
    for hexval in ("#3fa266", "#81a1c1", "#7bafe9", "#f1b467", "#dd7f76", "#fc6b83"):
        assert hexval in html.lower()
    for cls in (
        ".lv-great",
        ".lv-good",
        ".lv-expected",
        ".lv-warning",
        ".lv-threat",
        ".lv-critical",
    ):
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


def test_state_store_counts_durable_facts(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "state.db")
    assert asyncio.run(store.count_facts()) == 0
    asyncio.run(store.save_fact("the deck is gold", scope="global", source="manual"))
    asyncio.run(store.save_fact("tiers ascend", scope="global", source="manual"))
    assert asyncio.run(store.count_facts()) == 2


def test_runtimez_has_no_private_topology() -> None:
    source = Path("src/unigrok_public/server.py").read_text(encoding="utf-8")
    gate = source[source.index("async def runtimez") :]
    gate = gate[: gate.index("class CallerIdentityMiddleware")]
    assert '"tier_nav"' not in gate
    for key in ('"layer"', '"task_rag"', '"credential_planes"', '"fact_count"'):
        assert key in gate


def test_dashboard_consumes_public_runtime_truth() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    # Credential-planes posture renders server truth, threat when no plane.
    assert "rt?.credential_planes" in html
    assert "cp.effective_plane" in html
    # Notices are static server templates; warning severity maps to warning.
    assert "credential_planes?.notices" in html
    # Real RAG stats replace the sample placeholders.
    assert "wires from /runtimez" not in html
    assert "fact_count" in html
    # Local-runtime billing class carries the local plane blue.
    assert "local_runtime:{c:'#7bafe9'" in html
