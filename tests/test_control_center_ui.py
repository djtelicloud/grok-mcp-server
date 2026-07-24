"""Control Center honesty and pane-contract regression tests.

Pipeline plan: docs/design/ui-data-pipeline.md. The dashboard must render
runtime truth (readyz status verbatim), expose the plane/kind aggregates, and
carry the Time/Kind/Stop receipt columns.
"""

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


def test_command_drawer_structure_and_veil() -> None:
    # Drawer mirrors the control-site groups, anchor-scrolls to panels, and
    # hides contributor items on the public tier; sign-in links the control
    # origin and the deck never handles credentials.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="drawer"' in html and 'id="dmenu"' in html and 'id="backdrop"' in html
    for group in ("'This machine'", "'Your project'", "'Build'", "'Account'"):
        assert group in html
    assert "tierLevel>0||i.min===0" in html  # veil: public tier lists only public items
    # nav default + identity fallback + drawer sign-out
    assert html.count("https://control.grokmcp.org") == 3
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
    assert (
        "!registryAvailable?'<tr><td colspan=\"3\" class=\"empty\">"
        "Registry unavailable · —</td></tr>'"
    ) in html
    assert "rt?.state_backend||'sqlite'" not in html
    assert "Service unreachable" in html
    # independent per-feed catches so one failure can't sink the others
    assert html.count(".catch(()=>null)") >= 3


def test_tier_gating_reveal_is_pinned() -> None:
    # The hierarchical reveal is the load-bearing access rule; assert it
    # verbatim so deleting or inverting it fails.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "{public:0,sky:1,space:2}" in html
    assert "activeTier=runtimeTier" in html
    assert "$('sky-tier').style.display=tierLevel>=1?'':'none'" in html
    assert "$('space-tier').style.display=tierLevel>=2?'':'none'" in html
    assert "if(tierLevel>=1)renderSky()" in html
    assert "if(tierLevel>=2)renderSpace(" in html


def test_governance_and_build_panels() -> None:
    # Smart adds from real /runtimez data: build/durable metrics and the
    # policy/governance flags (spend-enabling reads warning).
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="build"' in html and 'id="policy"' in html
    assert "grok_build" in html and "api_spend_enforcement" in html
    assert "routing_advisor" in html and "automatic_judge_spend" in html


def test_contributor_sample_panels_gated() -> None:
    # Sky/Space contributor shells live INSIDE their tier sections (positional
    # containment), so tier gating actually governs them.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert (
        html.index('id="sky-tier"')
        < html.index('id="ghreviews"')
        < html.index('id="space-tier"')
        < html.index('id="reportcard"')
    )
    assert 'id="liverun"' in html and 'id="devices"' in html
    # sealed report card must not headline a fabricated non-floor claim
    assert "sealed" in html.lower() and "% floor" in html


def test_severity_scoring_handles_numeric_success() -> None:
    # SQLite serializes success as 0/1 ints; a strict ===false comparison would
    # silently drop every failed receipt from the standout ranking.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "r.success===false" not in html
    assert "r.verified&&!r.success" in html


def test_tier_nav_renders_all_three_surfaces() -> None:
    # Sponsor decision: the unified switcher shows all three tiers and links
    # each to its own surface port (public 4765, sky 4768, space 4769). Higher
    # tiers enforce their own auth on arrival; the nav is navigation, not access.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="tiernav"' in html
    # Full tuples, not bare ports (a port can appear in unrelated sample data).
    # Each tier carries its command name + eyebrow for the dynamic page title.
    for tup in (
        "{id:'public',label:'@grok',port:'4765',name:'GroundCommand'",
        "{id:'sky',label:'@skygrok',port:'4768',name:'SkyCommand'",
        "{id:'space',label:'@spacegrok',port:'4769',name:'SpaceCommand'",
    ):
        assert tup in html
    for verbose_label in (
        "@grok Public Core",
        "@skygrok Sky Observer",
        "@spacegrok Space Awareness",
    ):
        assert f"label:'{verbose_label}'" not in html
    # applyTier drives title/eyebrow/tab state from the active tier
    assert "function applyTier(" in html and "$('pagetitle').textContent" in html


def test_forge_nav_is_port_bound_and_space_is_advertised() -> None:
    # Forge uses the same native, per-origin navigation as every other surface.
    # Space stays visible as an access-dependent destination; the tab itself
    # keeps the exact terse label and is never replaced by a sample shell.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "function bindForgeSurface(rt)" in html
    assert "function isForgeSurface(rt)" in html
    assert "if(surface)return surface==='forge'" in html
    assert "if(rt.tier_nav)" in html
    assert "if(rt.tier_nav&&!forgeSurface)" not in html
    assert "tier-access" not in html
    assert "upgrade: opens SpaceCommand and its live data" in html
    assert "dataset.inshell" not in html
    assert "a.hidden=true" not in html
    assert "activeTier='sky'" not in html
    assert "function hydrateSkyLive(rt,b)" in html
    assert "No cross-port" in html or "no cross-port" in html
    assert "UI_BUILD" in html and "r30" in html


def test_mobile_tier_nav_is_compact_and_overflow_safe() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert '<nav class="tier-nav" id="tiernav" aria-label="UniGrok destinations">' in html
    assert '<span class="tier-label">Surface</span>' not in html
    assert ".tier-label{" not in html
    assert "grid-template-columns:44px repeat(3,minmax(0,1fr))" in html
    assert '.tier-tab[data-tier="public"]{grid-column:2;grid-row:1}' in html
    assert '.tier-tab[data-tier="sky"]{grid-column:3;grid-row:1}' in html
    assert '.tier-tab[data-tier="space"]{grid-column:4;grid-row:1}' in html
    assert ".snip2{grid-template-columns:minmax(0,1fr)}" in html
    assert ".kv{grid-template-columns:minmax(68px,auto) minmax(0,1fr)" in html
    assert ".kv b{min-width:0;overflow-wrap:anywhere}" in html
    assert "@media(max-width:360px){.tier-dot{display:none}" in html


def test_valid_zero_counts_never_render_as_missing_data() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "No data." not in html
    assert "emptyState={label:'events recorded',value:'0'}" in html
    assert "renderBuckets('fallbacks','fallbacks',()=>lvHex.warning,'fallback events'," in html
    assert "xs=>xs.filter(x=>x.name!=='unknown')" in html
    assert "<b>0 breaker events</b>" in html
    assert "Breaker telemetry unavailable" in html
    assert "telemetry unavailable" in html
    assert "0 receipts recorded — nothing to evaluate yet." in html
    assert "0 receipts recorded — activity appears after the first request." in html


def test_explicit_non_forge_surface_bypasses_legacy_port_fallback() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    detect = html[
        html.index("function isForgeSurface(rt)")
        : html.index("function bindForgeSurface(rt)")
    ]
    surface_truth = detect.index("if(surface)return surface==='forge'")
    legacy_fallback = detect.index("const pub=rt&&rt.tier_nav&&rt.tier_nav.public")
    assert surface_truth < legacy_fallback
    assert "String(rt?.surface||'').trim().toLowerCase()" in detect


def test_tier_tabs_use_native_navigation() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    click = html[
        html.index("document.addEventListener('click'")
        : html.index("document.addEventListener('keydown'")
    ]
    assert "tier-tab" not in click
    assert "selectTier(" not in html
    assert "syncPreviewUrl(" not in html
    assert "history.replaceState" not in html
    assert "if(nav.url){a.href=`${nav.url}/ui/`;}" in html
    assert "else if(nav.port)" in html


def test_space_unavailable_never_falls_back_to_same_origin_preview() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert ".get('preview')" not in html
    assert "?preview=" not in html
    assert "id==='space'?'upgrade: opens SpaceCommand" in html
    assert "const feedJson=async(r,allow503=false)" in html
    assert "fetchFeed('/readyz',true)" in html
    assert "fetchFeed('/benchmarkz')" in html
    assert "fetchFeed('/runtimez')" in html
    assert "connect-src 'self'" not in html  # CSP is server-owned, never widened here.


def test_sky_badge_tracks_live_payloads_without_hiding_samples() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    state = html[
        html.index("function setSkyHydrationState(")
        : html.index("function hydrateSkyLive(")
    ]
    hydrate = html[
        html.index("function hydrateSkyLive(")
        : html.index("function renderSky(")
    ]
    assert "if(tierLevel<1)return" in state
    assert "live.length?'MIXED':'SAMPLE'" in state
    assert "lanes','reviews','run" in state
    assert "hasBreakers=false,hasLatency=false" in hydrate
    assert "b?.telemetry&&typeof b.telemetry==='object'" in hydrate
    assert "SKY.breakers=null" in hydrate
    assert "SKY.latency=null" in hydrate
    assert "SKY.latencySamples===0" in hydrate
    assert "Number.isFinite(Number(rawP95))" in hydrate
    assert "setSkyHydrationState(hasBreakers,hasLatency)" in hydrate
    assert "return {breakers:hasBreakers,latency:hasLatency}" in hydrate


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
    # gate_id must be null (anchored — the OR-fallback version was tautological)
    assert "gate_id null" in html.lower()
    assert not re.findall(r"gate_id (?!null)\S+", html.lower())
    # every "sealed READY" occurrence must be negated ("no sealed READY")
    assert re.findall(r"(?<!no )sealed READY", html) == []


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


def test_tier_nav_ports_are_bounded_env_values() -> None:
    # tier_nav ports come from names-only env params; the hosted runtime
    # omits the block entirely (asserted via the cloudrun gate in runtimez).
    from unigrok_public.server import (
        PUBLIC_TIER_PORT,
        SKY_TIER_PORT,
        SPACE_TIER_PORT,
    )

    assert (PUBLIC_TIER_PORT, SKY_TIER_PORT, SPACE_TIER_PORT) == (4765, 4768, 4769)
    source = Path("src/unigrok_public/server.py").read_text(encoding="utf-8")
    gate = source[source.index("async def runtimez") :]
    gate = gate[: gate.index("class CallerIdentityMiddleware")]
    assert 'if not is_cloudrun_runtime():' in gate
    assert '"tier_nav"' in gate
    # /runtimez surfaces the tier feeds the deck consumes.
    for key in ('"layer"', '"surface"', '"task_rag"', '"credential_planes"', '"fact_count"'):
        assert key in gate
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    assert "UNIGROK_PUBLIC_PORT: ${UNIGROK_PUBLIC_PORT:-4765}" in compose
    assert "UNIGROK_PUBLIC_PORT: ${UNIGROK_PORT:-4765}" not in compose
    assert "UNIGROK_FORGE_PORT: ${UNIGROK_FORGE_PORT:-4766}" in compose
    assert "UNIGROK_FORGE_URL: ${UNIGROK_FORGE_URL:-}" in compose


def test_dashboard_consumes_server_tier_truth() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    # Server layer wins over port sniffing; nav targets come from tier_nav —
    # per-tier URLs first, same-host port fallback second.
    assert "function applyRuntimeTier(rt)" in html
    assert "rt.tier_nav" in html
    assert "nav.url" in html and "nav.port" in html
    assert "LEVEL[rt.layer]" in html
    assert "bindForgeSurface(rt)" in html
    assert "applyRuntimeTier(rt);" in html
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


def test_dashboard_identity_states_follow_gateway_truth() -> None:
    # Identity comes from the gateway's /api/me, never the browser's own
    # github.com session: 200 -> signed-in pill; Forge signed-out -> existing
    # Control OAuth first with the device-code path retained as fallback.
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "function applyIdentity(me)" in html
    assert "fetch('/api/me',{signal:AbortSignal.timeout(5000)})" in html
    assert "el.textContent=me.login" in html
    assert "Signed in · ${me.login}" not in html
    assert "Continue with Cloud" in html
    assert "el.href='/auth/control/start'" in html
    assert "location.replace('/auth/control/start')" in html
    assert "CLOUD_RESUME_KEY" in html
    assert "CLOUD_AUTO_KEY" in html
    assert "Cloud link reconnecting" in html
    assert "Cloud temporarily unavailable" in html
    assert "unavailable:true" in html
    assert "Use device code" in html
    # Fallback contract: device flow start/poll + one-time code chip, no
    # password fields, and honest failure states.
    assert "fetch('/auth/github/start',{method:'POST',headers:{'X-UniGrok-CSRF':'1'}})" in html
    assert "fetch('/auth/github/poll',{method:'POST'" in html
    assert "'X-UniGrok-CSRF':'1'" in html
    assert "github.com/login/device" in html
    assert "github_oauth_not_configured" in html
    assert "type=\"password\"" not in html and "type='password'" not in html
    # Public surface keeps the external control-site navigation.
    assert html.count("Open contributor control") == 2  # static anchor + JS branch
    assert "el.href='https://control.grokmcp.org'" in html


def test_identity_never_relabels_same_origin_data_as_another_tier() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    runtime = html[html.index("function applyRuntimeTier(rt)") :]
    runtime = runtime[: runtime.index("// One delegated click handler")]
    identity = html[html.index("function applyIdentity(me)") :]
    identity = identity[: identity.index("// Device-flow driver")]

    assert "let runtimeTier=portTier;" in html
    assert "function reconcileTier()" in html
    assert "runtimeTier=rt.layer" in runtime
    assert "activeTier=runtimeTier" in html
    assert "sessionTier" not in html
    assert "me.tier" not in identity
    assert "reconcileTier();" in runtime
    assert "reconcileTier();" in identity


def test_non_2xx_live_feeds_degrade_honestly() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "const feedJson=async(r,allow503=false)" in html
    assert "r.ok||(allow503&&r.status===503)" in html
    assert "fetchFeed('/readyz',true)" in html
    for endpoint in ("benchmarkz", "runtimez"):
        assert f"fetchFeed('/{endpoint}')" in html
    assert "AbortSignal.timeout(5000)" in html


def test_every_live_container_starts_non_blank_and_telemetry_is_field_scoped() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "function primeDataContainers()" in html
    assert "Loading live data …" in html
    assert "const numberAvailable=k=>" in html
    assert "const bucketAvailable=k=>" in html
    assert "receiptTelemetryAvailable" in html
    assert "outcomeAvailable" in html
    assert "breakerTelemetryAvailable" in html


def test_empty_tool_registry_is_distinct_from_missing_registry() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "const registryAvailable=!!rt&&Array.isArray(rt.tools)" in html
    assert "0 tools exposed by this runtime." in html
    assert "Registry unavailable · —" in html
    assert "0 destructive tools among " in html
