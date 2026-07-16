# tests/test_multiagent.py
# Multi-agent substrate: caller identity capture (MCP clientInfo, HTTP
# X-Caller/auth-key alias), schema v8 telemetry attribution, per-caller daily
# budgets, the grok://workspace resource, and per-caller metrics segmentation.

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from src.http_server import (
    CallerContextMiddleware,
    _aggregate_telemetry_callers,
    _derive_http_caller,
    _derive_http_principal,
    create_app,
)
from src.jobs import JobManager
from src.identity import (
    caller_from_mcp_context,
    get_active_caller,
    get_active_principal,
    normalize_caller,
    normalize_principal,
    reset_active_caller,
    reset_active_principal,
    set_active_caller,
    set_active_principal,
    telemetry_row_caller,
)
from src.utils import (
    CallerBudgetExceeded,
    GrokSessionStore,
    MetaLayer,
    enforce_caller_budget,
    orchestrate,
    run_agent_turn,
)


@pytest.fixture
async def cstore(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "callers.db")
    yield s
    await s.close()


def _ctx_with_client_info(name="claude-code", version="1.2.3"):
    """A minimal FastMCP-Context-shaped object: ctx.session.client_params is
    the InitializeRequestParams whose clientInfo carries name/version."""
    return SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(
                clientInfo=SimpleNamespace(name=name, version=version)
            )
        )
    )


class _CtxOutsideRequest:
    """Mimics mcp 1.26's Context.session outside a request: the property
    raises instead of returning a session."""

    @property
    def session(self):
        raise ValueError("Context is not available outside of a request")


# ─────────────────────────────────────────────────────────────────────────────
# Item 1 — caller identity capture
# ─────────────────────────────────────────────────────────────────────────────

class TestCallerFromContext:
    def test_client_info_present(self):
        assert caller_from_mcp_context(_ctx_with_client_info(name="codex-cli")) == "codex-cli"

    def test_client_params_absent(self):
        """stdio clients that never completed initialize: client_params is
        None and the caller degrades to None."""
        ctx = SimpleNamespace(session=SimpleNamespace(client_params=None))
        assert caller_from_mcp_context(ctx) is None

    def test_context_outside_request_degrades_to_none(self):
        assert caller_from_mcp_context(_CtxOutsideRequest()) is None

    def test_blank_name_degrades_to_none(self):
        assert caller_from_mcp_context(_ctx_with_client_info(name="   ")) is None

    def test_normalize_caller_strips_and_bounds(self):
        assert normalize_caller("  gemini\x00\n-agent  ") == "gemini-agent"
        assert normalize_caller("x" * 500) == "x" * 80
        assert normalize_caller(None) is None
        assert normalize_caller("   ") is None

    def test_long_principals_keep_collision_resistant_suffixes(self):
        left = "subject:" + ("x" * 300) + "left"
        right = "subject:" + ("x" * 300) + "right"
        assert normalize_principal(left) != normalize_principal(right)
        assert len(normalize_principal(left)) == 240


class TestActiveCallerContext:
    def test_utils_compatibility_exports_share_identity_contextvars(self):
        import src.identity as identity_module
        import src.utils as utils_module

        assert utils_module._ACTIVE_CALLER is identity_module._ACTIVE_CALLER
        assert utils_module._ACTIVE_PRINCIPAL is identity_module._ACTIVE_PRINCIPAL
        assert utils_module._ACTIVE_CLIENT_ID is identity_module._ACTIVE_CLIENT_ID
        assert utils_module._ACTIVE_SESSION_ID is identity_module._ACTIVE_SESSION_ID

    def test_set_get_reset_roundtrip(self):
        token = set_active_caller("claude-code")
        try:
            assert get_active_caller() == "claude-code"
        finally:
            reset_active_caller(token)
        assert get_active_caller() is None

    def test_principal_set_get_reset_roundtrip(self):
        token = set_active_principal("oauth:github:42")
        try:
            assert get_active_principal() == "oauth:github:42"
        finally:
            reset_active_principal(token)
        assert get_active_principal() is None


class TestAgentToolCapturesCaller:
    @pytest.mark.asyncio
    async def test_ctx_client_info_forwarded(self, monkeypatch):
        from src.tools.chats import agent

        mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

        await agent(task="do it", ctx=_ctx_with_client_info(name="claude-code"))

        assert mock_run.call_args.kwargs["caller"] == "claude-code"

    @pytest.mark.asyncio
    async def test_no_ctx_passes_none(self, monkeypatch):
        from src.tools.chats import agent

        mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

        await agent(task="do it")

        assert mock_run.call_args.kwargs["caller"] is None


class TestRunAgentTurnCaller:
    @pytest.mark.asyncio
    async def test_caller_forwarded_to_orchestrate_and_session_metadata(self, monkeypatch):
        """The caller flows into orchestrate (telemetry attribution) AND onto
        the persisted assistant message metadata for the session."""
        from src.utils import store

        mock_orchestrate = AsyncMock(
            return_value=MetaLayer(generation="answer", finish_reason="final_answer")
        )
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        try:
            await run_agent_turn(prompt="hi", session="ma-caller-sess", caller="claude-code")

            assert mock_orchestrate.call_args.kwargs["caller"] == "claude-code"
            messages = await store.load_messages("ma-caller-sess")
            assistant = [m for m in messages if m["role"] == "assistant"][-1]
            assert assistant["metadata"]["caller"] == "claude-code"
        finally:
            await store.delete_session("ma-caller-sess")

    @pytest.mark.asyncio
    async def test_context_bound_caller_used_as_fallback(self, monkeypatch):
        """No explicit caller param: run_agent_turn picks up whatever the
        transport bound to the async context (the HTTP middleware path)."""
        mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        token = set_active_caller("http:key-deadbeef")
        try:
            await run_agent_turn(prompt="hi")
        finally:
            reset_active_caller(token)

        assert mock_orchestrate.call_args.kwargs["caller"] == "http:key-deadbeef"

    @pytest.mark.asyncio
    async def test_no_caller_stays_none(self, monkeypatch):
        """Nothing bound anywhere: caller=None everywhere, no metadata key."""
        from src.utils import store

        mock_orchestrate = AsyncMock(
            return_value=MetaLayer(generation="answer", finish_reason="final_answer")
        )
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        try:
            await run_agent_turn(prompt="hi", session="ma-anon-sess")

            assert mock_orchestrate.call_args.kwargs["caller"] is None
            messages = await store.load_messages("ma-anon-sess")
            assistant = [m for m in messages if m["role"] == "assistant"][-1]
            assert "caller" not in (assistant.get("metadata") or {})
        finally:
            await store.delete_session("ma-anon-sess")

    @pytest.mark.asyncio
    async def test_http_principal_attribution_overrides_explicit_mcp_label(self, monkeypatch):
        """FastMCP clientInfo is caller-controlled and cannot replace the
        identity bound by authenticated HTTP middleware."""
        mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        caller_token = set_active_caller("http:key-1|vscode")
        principal_token = set_active_principal("http:key-1")
        try:
            await run_agent_turn(prompt="hi", caller="spoofed-client-info")
        finally:
            reset_active_principal(principal_token)
            reset_active_caller(caller_token)

        assert mock_orchestrate.call_args.kwargs["caller"] == "http:key-1|vscode"


# ─────────────────────────────────────────────────────────────────────────────
# Schema v8 — telemetry metadata/created_at, jobs caller
# ─────────────────────────────────────────────────────────────────────────────

class TestV8Migration:
    @pytest.mark.asyncio
    async def test_v8_columns_and_index(self, cstore):
        await cstore._ensure_initialized()
        async with cstore._conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row[0] >= 8
        async with cstore._conn.execute("PRAGMA table_info(telemetry);") as cursor:
            telemetry_cols = {r[1] for r in await cursor.fetchall()}
        assert {"metadata", "created_at"} <= telemetry_cols
        async with cstore._conn.execute("PRAGMA table_info(jobs);") as cursor:
            jobs_cols = {r[1] for r in await cursor.fetchall()}
        assert "caller" in jobs_cols
        async with cstore._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index';"
        ) as cursor:
            indexes = {r[0] for r in await cursor.fetchall()}
        assert "idx_telemetry_created_at" in indexes

    @pytest.mark.asyncio
    async def test_save_telemetry_with_caller_writes_metadata(self, cstore):
        await cstore.save_telemetry("intent", "API", 1, 0.5, 0.01, caller="claude-code")
        rows = await cstore.get_telemetry_stats()
        assert json.loads(rows[0]["metadata"]) == {"caller": "claude-code"}
        assert rows[0]["created_at"]

    @pytest.mark.asyncio
    async def test_save_telemetry_without_caller_keeps_metadata_null(self, cstore):
        await cstore.save_telemetry("intent", "API", 1, 0.5, 0.01)
        rows = await cstore.get_telemetry_stats()
        assert rows[0]["metadata"] is None
        assert telemetry_row_caller(rows[0]) is None

    @pytest.mark.asyncio
    async def test_save_telemetry_falls_back_to_bound_context_caller(self, cstore):
        """The src/storage.py contract: caller=None reads the ambient
        contextvar, so indirect telemetry writers (thinking loop, history
        compaction) stay attributed without threading the param."""
        token = set_active_caller("gemini-agent")
        try:
            await cstore.save_telemetry("intent", "API", 1, 0.5, 0.01)
        finally:
            reset_active_caller(token)
        rows = await cstore.get_telemetry_stats()
        assert telemetry_row_caller(rows[0]) == "gemini-agent"

    def test_telemetry_row_caller_handles_both_shapes(self):
        assert telemetry_row_caller({"metadata": '{"caller":"codex"}'}) == "codex"
        assert telemetry_row_caller({"metadata": {"caller": "codex"}}) == "codex"
        assert telemetry_row_caller({"metadata": "not-json{"}) is None
        assert telemetry_row_caller({"metadata": None}) is None
        assert telemetry_row_caller({}) is None


class TestJobCaller:
    @pytest.mark.asyncio
    async def test_job_row_records_explicit_caller(self, cstore, monkeypatch):
        monkeypatch.setattr(JobManager, "_run_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=cstore)

        view = await manager.submit("find things", caller="codex-cli")
        await manager.wait(view["job_id"])

        row = await cstore.get_job(view["job_id"])
        assert row["caller"] == "codex-cli"
        assert JobManager.describe(row)["caller"] == "codex-cli"

    @pytest.mark.asyncio
    async def test_job_caller_falls_back_to_bound_context(self, cstore, monkeypatch):
        monkeypatch.setattr(JobManager, "_run_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=cstore)

        token = set_active_caller("gemini-agent")
        try:
            view = await manager.submit("find things")
        finally:
            reset_active_caller(token)
        await manager.wait(view["job_id"])

        row = await cstore.get_job(view["job_id"])
        assert row["caller"] == "gemini-agent"

    @pytest.mark.asyncio
    async def test_job_without_caller_stays_none(self, cstore, monkeypatch):
        monkeypatch.setattr(JobManager, "_run_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=cstore)

        view = await manager.submit("find things")
        await manager.wait(view["job_id"])

        row = await cstore.get_job(view["job_id"])
        assert row["caller"] is None
        assert "caller" not in JobManager.describe(row)

    @pytest.mark.asyncio
    async def test_create_job_itself_falls_back_to_bound_context(self, cstore):
        """The src/storage.py contract: create_job(caller=None) reads the
        ambient contextvar directly, so job writers that never thread the
        param (e.g. distill submissions) stay attributed."""
        token = set_active_caller("codex-cli")
        try:
            await cstore.create_job("job-ambient", prompt="p", model="m")
        finally:
            reset_active_caller(token)

        row = await cstore.get_job("job-ambient")
        assert row["caller"] == "codex-cli"

    @pytest.mark.asyncio
    async def test_submit_research_job_forwards_ctx_caller(self, monkeypatch):
        from src.tools.research import submit_research_job

        fake_manager = MagicMock()
        fake_manager.submit = AsyncMock(
            return_value={"job_id": "j1", "status": "queued", "model": "m"}
        )
        monkeypatch.setattr("src.tools.research.get_job_manager", lambda: fake_manager)

        await submit_research_job(prompt="dig in", ctx=_ctx_with_client_info(name="claude-code"))

        assert fake_manager.submit.call_args.kwargs["caller"] == "claude-code"

    @pytest.mark.asyncio
    async def test_submit_research_job_ctx_hidden_from_schema(self):
        """FastMCP injects ctx via the Context annotation; it must not leak
        into the tool's public input schema."""
        from mcp.server.fastmcp import FastMCP
        from src.tools.research import submit_research_job

        probe = FastMCP("schema-probe")
        probe.add_tool(submit_research_job)
        tools = {tool.name: tool for tool in await probe.list_tools()}

        properties = tools["submit_research_job"].inputSchema.get("properties", {})
        assert "ctx" not in properties
        assert "prompt" in properties


# ─────────────────────────────────────────────────────────────────────────────
# Item 2 — per-caller daily budgets
# ─────────────────────────────────────────────────────────────────────────────

class TestCallerCostToday:
    @pytest.mark.asyncio
    async def test_sums_only_matching_todays_rows(self, cstore):
        await cstore.save_telemetry("a", "API", 1, 0.1, 0.02, caller="codex-cli")
        await cstore.save_telemetry("b", "API", 0, 0.1, 0.03, caller="Codex-CLI")
        await cstore.save_telemetry("c", "API", 1, 0.1, 0.5, caller="claude-code")
        await cstore.save_telemetry("d", "API", 1, 0.1, 0.9)  # unattributed

        assert await cstore.get_caller_cost_today("codex-cli") == pytest.approx(0.05)
        assert await cstore.get_caller_cost_today("claude-code") == pytest.approx(0.5)
        assert await cstore.get_caller_cost_today("codex") == 0.0
        assert await cstore.get_caller_cost_today("nobody") == 0.0
        assert await cstore.get_caller_cost_today("") == 0.0

    @pytest.mark.asyncio
    async def test_principal_cost_includes_its_labels_but_not_label_injection(self, cstore):
        await cstore.save_telemetry(
            "a", "API", 1, 0.1, 0.02, caller="http:key-1|vscode"
        )
        await cstore.save_telemetry(
            "b", "API", 1, 0.1, 0.03, caller="http:key-1|http%3Akey-2"
        )
        await cstore.save_telemetry(
            "c", "API", 1, 0.1, 0.50, caller="http:key-2|vscode"
        )

        assert await cstore.get_caller_cost_today("http:key-1") == pytest.approx(0.05)
        assert await cstore.get_caller_cost_today("http:key-2") == pytest.approx(0.50)

    @pytest.mark.asyncio
    async def test_excludes_rows_from_earlier_days(self, cstore):
        await cstore.save_telemetry("a", "API", 1, 0.1, 0.02, caller="codex-cli")
        # Backdate the row to yesterday-ish; the created_at window must skip it.
        async with cstore._lock:
            await cstore._conn.execute(
                "UPDATE telemetry SET created_at = '2000-01-01T00:00:00'"
            )
            await cstore._conn.commit()

        assert await cstore.get_caller_cost_today("codex-cli") == 0.0


class TestBudgetEnforcement:
    @pytest.mark.asyncio
    async def test_env_unset_skips_entirely(self, monkeypatch):
        """No UNIGROK_CALLER_BUDGETS: the gate returns before touching the
        store — zero hot-path cost by default."""
        monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)
        exploding_store = MagicMock()
        exploding_store.get_caller_cost_today = AsyncMock(side_effect=RuntimeError("no!"))

        await enforce_caller_budget(exploding_store, "codex-cli")

        exploding_store.get_caller_cost_today.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_under_at_over_boundary(self, cstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"codex-cli": 0.10}))
        import src.utils as utils_module

        # Under budget: passes.
        await cstore.save_telemetry("a", "API", 1, 0.1, 0.05, caller="codex-cli")
        await enforce_caller_budget(cstore, "codex-cli")

        # Exactly AT budget: blocked ('at/over budget' contract).
        utils_module._CALLER_SPEND_CACHE.clear()
        await cstore.save_telemetry("b", "API", 1, 0.1, 0.05, caller="codex-cli")
        with pytest.raises(CallerBudgetExceeded, match=r"daily budget exhausted for codex-cli: \$0\.10/\$0\.10"):
            await enforce_caller_budget(cstore, "codex-cli")

        # Over budget: blocked.
        utils_module._CALLER_SPEND_CACHE.clear()
        await cstore.save_telemetry("c", "API", 1, 0.1, 0.05, caller="codex-cli")
        with pytest.raises(CallerBudgetExceeded):
            await enforce_caller_budget(cstore, "codex-cli")

    @pytest.mark.asyncio
    async def test_non_matching_caller_unaffected(self, cstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"codex": 0.0}))
        await enforce_caller_budget(cstore, "claude-code")  # no raise

    @pytest.mark.asyncio
    async def test_budget_entries_match_exact_principals(self, cstore, monkeypatch):
        monkeypatch.setenv(
            "UNIGROK_CALLER_BUDGETS", json.dumps({"codex": 0.0, "codex-cli": 5.0})
        )
        # The broad partial key cannot govern a different principal.
        await enforce_caller_budget(cstore, "codex-cli")

    @pytest.mark.asyncio
    async def test_spend_is_cached_per_entry(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"codex-cli": 1.0}))
        import src.utils as utils_module

        counting_store = MagicMock()
        counting_store.get_caller_cost_today = AsyncMock(return_value=0.01)

        await enforce_caller_budget(counting_store, "codex-cli")
        await enforce_caller_budget(counting_store, "codex-cli")
        assert counting_store.get_caller_cost_today.await_count == 1

        # Expire the cache entry: the next check re-queries.
        spent, fetched_at = utils_module._CALLER_SPEND_CACHE["codex-cli"]
        utils_module._CALLER_SPEND_CACHE["codex-cli"] = (spent, fetched_at - 120.0)
        await enforce_caller_budget(counting_store, "codex-cli")
        assert counting_store.get_caller_cost_today.await_count == 2

    @pytest.mark.asyncio
    async def test_store_failure_fails_closed_for_budgeted_caller(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"codex-cli": 1.0}))
        broken_store = MagicMock()
        broken_store.get_caller_cost_today = AsyncMock(side_effect=RuntimeError("db gone"))

        with pytest.raises(CallerBudgetExceeded) as raised:
            await enforce_caller_budget(broken_store, "codex-cli")

        assert str(raised.value) == (
            "daily budget check unavailable; configured cap is fail-closed"
        )
        assert "db gone" not in str(raised.value)

    @pytest.mark.asyncio
    async def test_malformed_budgets_env_ignored(self, cstore, monkeypatch):
        monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", "{not json")
        await enforce_caller_budget(cstore, "codex-cli")  # no raise

    @pytest.mark.asyncio
    async def test_orchestrate_blocks_before_any_model_work(self, cstore, monkeypatch):
        """The gate runs at the top of orchestrate: an exhausted caller gets
        the catchable CallerBudgetExceeded and no client is ever touched."""
        monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"codex-cli": 0.05}))
        await cstore.save_telemetry("a", "API", 1, 0.1, 0.06, caller="codex-cli")

        mock_client = MagicMock()
        with patch("src.utils.get_xai_client", return_value=mock_client):
            with pytest.raises(CallerBudgetExceeded, match="daily budget exhausted for codex-cli"):
                await orchestrate("hello there", store=cstore, caller="codex-cli")

        mock_client.chat.create.assert_not_called()
        # Only the seeded row exists — the blocked turn recorded nothing.
        assert len(await cstore.get_telemetry_stats()) == 1

    @pytest.mark.asyncio
    async def test_http_principal_owns_budget_not_client_label(self, cstore, monkeypatch):
        """A caller-controlled IDE label cannot select or evade an HTTP
        principal's budget pot."""
        monkeypatch.setenv(
            "UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-1": 0.0, "victim-ide": 5.0})
        )
        import src.utils as utils_module

        utils_module._CALLER_SPEND_CACHE.clear()
        principal_token = set_active_principal("http:key-1")
        try:
            with pytest.raises(
                CallerBudgetExceeded,
                match=r"daily budget exhausted for http:key-1",
            ):
                await orchestrate(
                    "hello there",
                    store=cstore,
                    caller="victim-ide",
                )
        finally:
            reset_active_principal(principal_token)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP gateway — X-Caller / auth-key alias propagation
# ─────────────────────────────────────────────────────────────────────────────

class TestHttpCallerDerivation:
    def test_x_caller_header_wins(self):
        scope = {
            "type": "http",
            "headers": [(b"x-caller", b"codex-cli"), (b"authorization", b"Bearer k")],
        }
        assert _derive_http_caller(scope) == "http:anon|codex-cli"

    def test_auth_key_alias_when_no_header(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_API_KEYS", "sekret-key")
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer sekret-key")]}
        assert _derive_http_caller(scope) == "http:key-1"

    def test_auth_key_alias_tracks_configured_key_order(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_API_KEYS", "first-key,second-key")
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer second-key")]}
        assert _derive_http_caller(scope) == "http:key-2"

    def test_anonymous_fallback(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        assert _derive_http_caller({"type": "http", "headers": []}) == "http:anon"

    def test_principal_ignores_self_asserted_identity_headers(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_API_KEYS", "first-key,second-key")
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer second-key"),
                (b"x-client-id", b"victim-ide"),
                (b"x-caller", b"victim-user"),
            ],
        }
        assert _derive_http_principal(scope) == "http:key-2"
        assert _derive_http_caller(scope) == "http:key-2|victim-ide"

    def test_oauth_subject_is_principal_even_with_spoofed_headers(self):
        scope = {
            "type": "http",
            "headers": [
                (b"x-client-id", b"victim-ide"),
                (b"x-caller", b"victim-user"),
            ],
            "unigrok.oauth": {"sub": "github:42"},
        }
        assert _derive_http_principal(scope) == "oauth:github:42"
        assert _derive_http_caller(scope) == "oauth:github:42|victim-ide"

    def test_client_label_cannot_poison_another_principals_budget_key(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_API_KEYS", "first-key")
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer first-key"),
                (b"x-client-id", b"http:key-2"),
            ],
        }
        caller = _derive_http_caller(scope)
        assert caller == "http:key-1|http%3Akey-2"
        assert "http:key-2" not in caller

    @pytest.mark.asyncio
    async def test_middleware_binds_and_resets_for_any_path(self):
        """The caller context middleware covers every HTTP path — including
        the /mcp mount — and always resets after the request."""
        seen = {}

        async def inner_app(scope, receive, send):
            seen["caller"] = get_active_caller()
            seen["principal"] = get_active_principal()

        middleware = CallerContextMiddleware(inner_app)
        scope = {"type": "http", "path": "/mcp", "headers": [(b"x-caller", b"gemini-agent")]}
        await middleware(scope, None, None)

        assert seen["caller"] == "http:anon|gemini-agent"
        assert seen["principal"] == "http:anon"
        assert get_active_caller() is None
        assert get_active_principal() is None

    def test_caller_middleware_is_pure_asgi(self):
        """Same tombstone as the other gateway middleware: BaseHTTPMiddleware
        interferes with SSE client disconnects on the /mcp mount."""
        from starlette.middleware.base import BaseHTTPMiddleware

        assert not issubclass(CallerContextMiddleware, BaseHTTPMiddleware)


class TestGatewayCallerPropagation:
    def _run_request(self, monkeypatch, headers):
        import src.http_server as http_module

        seen = {}

        async def fake_run_agent_turn(**kwargs):
            # The contextvar bound by the middleware must be visible inside
            # the agent turn (this is what attributes telemetry downstream).
            seen["caller"] = get_active_caller()
            return MetaLayer(generation="ok", finish_reason="final_answer")

        monkeypatch.setattr(http_module, "run_agent_turn", fake_run_agent_turn)
        with TestClient(create_app()) as client:
            res = client.post(
                "/v1/chat/completions",
                json={"model": "unigrok-agent", "messages": [{"role": "user", "content": "hi"}]},
                headers=headers,
            )
        assert res.status_code == 200
        return seen["caller"]

    def test_x_caller_header_propagates_to_agent_turn(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

        caller = self._run_request(monkeypatch, {"X-Caller": "codex-cli"})
        assert caller == "http:anon|codex-cli"

    def test_auth_key_alias_propagates_without_header(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.setenv("UNIGROK_API_KEYS", "sekret-key")
        monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)

        caller = self._run_request(
            monkeypatch, {"Authorization": "Bearer sekret-key"}
        )
        assert caller == "http:key-1"

    def test_anonymous_request_reads_http_anon(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

        assert self._run_request(monkeypatch, {}) == "http:anon"

    def test_x_caller_propagates_through_real_mcp_mount(self, monkeypatch):
        """End-to-end pin of the contextvar-inheritance claim: the stateless
        /mcp server task is spawned from the request context, so the caller
        bound by the middleware is visible inside the MCP tool handler."""
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        import src.http_server as http_module

        seen = {}

        async def fake_run_agent_turn(**kwargs):
            seen["caller"] = get_active_caller()
            return MetaLayer(generation="ok", finish_reason="final_answer")

        monkeypatch.setattr(http_module, "run_agent_turn", fake_run_agent_turn)

        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "agent", "arguments": {"prompt": "hi"}},
        }
        # base_url must carry host:port — the MCP transport security layer
        # validates the Host header (DNS-rebinding protection).
        with TestClient(create_app(), base_url="http://localhost:8080") as client:
            res = client.post(
                "/mcp",
                json=call,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                    "X-Caller": "gemini-agent",
                },
            )

        assert res.status_code == 200
        assert seen["caller"] == "http:anon|gemini-agent"


# ─────────────────────────────────────────────────────────────────────────────
# Item 3 — grok://workspace resource
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkspaceResource:
    @pytest.mark.asyncio
    async def test_registered_and_carries_all_sections(self):
        from src.server import mcp
        import src.tools.resources as resources_module

        uris = {str(res.uri) for res in await mcp.list_resources()}
        assert "grok://workspace" in uris

        resources_module._workspace_git_cache.update({"at": 0.0, "text": ""})
        contents = list(await mcp.read_resource("grok://workspace"))
        text = contents[0].content

        assert "# UniGrok Workspace" in text
        # Real .agents/AGENTS.md content is embedded (banner + coordination).
        assert "Talk to humans first" in text
        assert "Multi-Agent Git Coordination" in text
        # .gemini/GEMINI.md exists in this repo and is embedded too.
        assert "## Gemini Agent Notes (.gemini/GEMINI.md)" in text
        assert "## Git" in text and "Last 5 commits" in text
        assert "## Active Sessions" in text
        assert "## Runtime State" in text

    @pytest.mark.asyncio
    async def test_output_is_bounded(self, monkeypatch):
        """Even with pathologically large agent docs, the assembled document
        never exceeds the total clamp."""
        from src.server import mcp
        import src.tools.resources as resources_module

        monkeypatch.setattr(
            resources_module, "_read_agent_doc", lambda rel_path: "x" * 100_000
        )
        contents = list(await mcp.read_resource("grok://workspace"))
        text = contents[0].content

        assert len(text) <= resources_module._WORKSPACE_TOTAL_LIMIT + 60
        assert "truncated at" in text

    @pytest.mark.asyncio
    async def test_git_summary_is_cached(self, monkeypatch):
        import src.tools.resources as resources_module

        calls = {"branch": 0}

        async def fake_branch(repo_path=None):
            calls["branch"] += 1
            return "claude/ma-branch"

        async def fake_log(limit=10, repo_path=None):
            return "abc123 first commit"

        monkeypatch.setattr("src.tools.git.git_current_branch", fake_branch)
        monkeypatch.setattr("src.tools.git.git_log", fake_log)
        resources_module._workspace_git_cache.update({"at": 0.0, "text": ""})
        try:
            first = await resources_module._workspace_git_summary()
            second = await resources_module._workspace_git_summary()
        finally:
            resources_module._workspace_git_cache.update({"at": 0.0, "text": ""})

        assert "claude/ma-branch" in first
        assert second == first
        assert calls["branch"] == 1

    @pytest.mark.asyncio
    async def test_git_failure_degrades_to_unavailable(self, monkeypatch):
        import src.tools.resources as resources_module

        async def broken(*args, **kwargs):
            raise RuntimeError("git exploded")

        monkeypatch.setattr("src.tools.git.git_current_branch", broken)
        monkeypatch.setattr("src.tools.git.git_log", broken)
        resources_module._workspace_git_cache.update({"at": 0.0, "text": ""})
        try:
            text = await resources_module._workspace_git_summary()
        finally:
            resources_module._workspace_git_cache.update({"at": 0.0, "text": ""})

        assert "unavailable" in text


# ─────────────────────────────────────────────────────────────────────────────
# Item 4 — segmented metrics
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsSegmentation:
    def test_aggregate_telemetry_callers(self):
        rows = [
            {"success": 1, "cost": 0.01, "metadata": '{"caller":"claude-code"}'},
            {"success": 0, "cost": 0.02, "metadata": {"caller": "claude-code"}},
            {"success": 1, "cost": 0.5, "metadata": '{"caller":"codex-cli"}'},
            {"success": 1, "cost": 0.9, "metadata": None},  # unattributed old row
        ]
        callers = _aggregate_telemetry_callers(rows)

        assert set(callers) == {"claude-code", "codex-cli"}
        assert callers["claude-code"]["requests"] == 2
        assert callers["claude-code"]["verified_outcomes"] == 2
        assert callers["claude-code"]["unverified_requests"] == 0
        assert callers["claude-code"]["success_rate"] == 0.5
        assert callers["claude-code"]["total_cost_usd"] == pytest.approx(0.03)
        assert callers["codex-cli"]["requests"] == 1

    def test_aggregate_is_bounded_to_top_callers(self):
        from src.http_server import _METRICS_TOP_CALLERS

        rows = [
            {"success": 1, "cost": 0.0, "metadata": {"caller": f"agent-{i}"}}
            for i in range(_METRICS_TOP_CALLERS + 15)
        ]
        assert len(_aggregate_telemetry_callers(rows)) == _METRICS_TOP_CALLERS

    def test_metrics_endpoint_reports_callers(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        import src.http_server as http_module

        rows = [
            {"chosen_plane": "API", "success": 1, "latency": 1.0, "cost": 0.01,
             "metadata": '{"caller":"claude-code"}'},
            {"chosen_plane": "API", "success": 0, "latency": 2.0, "cost": 0.02,
             "metadata": '{"caller":"claude-code"}'},
            {"chosen_plane": "CLI", "success": 1, "latency": 0.5, "cost": 0.0},
        ]
        monkeypatch.setattr(
            http_module.store, "get_telemetry_stats", AsyncMock(return_value=rows)
        )

        with TestClient(
            create_app(),
            base_url="http://localhost:8080",
            client=("127.0.0.1", 50000),
        ) as client:
            res = client.get("/metrics")

        assert res.status_code == 200
        payload = res.json()
        assert payload["callers"]["claude-code"]["requests"] == 2
        assert payload["callers"]["claude-code"]["success_rate"] == 0.5
        assert payload["callers"]["claude-code"]["total_cost_usd"] == pytest.approx(0.03)
        # Plane aggregates keep working alongside the caller segmentation.
        assert payload["planes"]["API"]["requests"] == 2

    @pytest.mark.asyncio
    async def test_caller_stats_today_aggregates_and_ranks(self, cstore):
        await cstore.save_telemetry("a", "API", 1, 0.1, 0.01, caller="claude-code")
        await cstore.save_telemetry("b", "API", 0, 0.1, 0.02, caller="claude-code")
        await cstore.save_telemetry("u", "API", None, 0.1, 0.04, caller="claude-code")
        await cstore.save_telemetry(
            "history-compaction", "API", 1, 0.1, 0.50, caller="claude-code"
        )
        await cstore.save_telemetry("c", "API", 1, 0.1, 0.30, caller="codex-cli")
        await cstore.save_telemetry("d", "API", 1, 0.1, 0.90)  # unattributed

        stats = await cstore.get_caller_stats_today(limit=5)

        assert [row["caller"] for row in stats] == ["claude-code", "codex-cli"]
        assert stats[0]["requests"] == 3
        assert stats[0]["verified_outcomes"] == 2
        assert stats[0]["success_rate"] == 0.5
        assert stats[0]["total_cost_usd"] == pytest.approx(0.57)
        assert stats[1]["total_cost_usd"] == pytest.approx(0.30)

    @pytest.mark.asyncio
    async def test_status_shows_top_callers_today(self, monkeypatch):
        import src.tools.system as system_module
        from src.tools.system import grok_mcp_status

        monkeypatch.setattr(
            system_module.store,
            "get_caller_stats_today",
            AsyncMock(return_value=[
                {"caller": "claude-code", "requests": 3, "success_rate": 1.0, "total_cost_usd": 0.012},
            ]),
        )

        status = await grok_mcp_status()

        assert "Top Callers Today" in status
        assert "`claude-code`: 3 reqs, 100% success (3 verified), $0.0120" in status

    @pytest.mark.asyncio
    async def test_status_top_callers_degrade_to_none(self, monkeypatch):
        import src.tools.system as system_module
        from src.tools.system import grok_mcp_status

        monkeypatch.setattr(
            system_module.store,
            "get_caller_stats_today",
            AsyncMock(side_effect=RuntimeError("db offline")),
        )

        status = await grok_mcp_status()

        assert "**Top Callers Today:** `none`" in status


# ─────────────────────────────────────────────────────────────────────────────
# HTTP gateway — X-Client-ID identity and per-IDE session scoping
# ─────────────────────────────────────────────────────────────────────────────

class TestClientIdDerivation:
    def test_x_client_id_wins_over_x_caller_and_key(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_API_KEYS", "sekret-key")
        scope = {
            "type": "http",
            "headers": [
                (b"x-client-id", b"vscode"),
                (b"x-caller", b"codex-cli"),
                (b"authorization", b"Bearer sekret-key"),
            ],
        }
        assert _derive_http_caller(scope) == "http:key-1|vscode"

    def test_scoped_session_prefixes_only_with_client_bound(self):
        import src.http_server as http_module

        token = http_module._ACTIVE_CLIENT_ID.set("vscode")
        session_token = http_module._ACTIVE_SESSION_ID.set("session-from-header")
        try:
            assert http_module._scoped_session("main") == "vscode:main"
            # Fallback to in-flight header session if None/empty passed
            assert http_module._scoped_session(None) == "vscode:session-from-header"
            assert http_module._scoped_session("") == "vscode:session-from-header"
            # Idempotent: an already-scoped name is not double-prefixed.
            assert http_module._scoped_session("vscode:main") == "vscode:main"
        finally:
            http_module._ACTIVE_SESSION_ID.reset(session_token)
            http_module._ACTIVE_CLIENT_ID.reset(token)

    def test_scoped_session_binds_principal_above_client_label(self):
        import src.http_server as http_module

        principal_token = set_active_principal("oauth:github:42")
        client_token = http_module._ACTIVE_CLIENT_ID.set("vscode")
        try:
            assert http_module._scoped_session("main") == "oauth%3Agithub%3A42:vscode:main"
            assert (
                http_module._scoped_session("oauth%3Agithub%3A42:vscode:main")
                == "oauth%3Agithub%3A42:vscode:main"
            )
        finally:
            http_module._ACTIVE_CLIENT_ID.reset(client_token)
            reset_active_principal(principal_token)

    def test_scoped_session_untouched_without_client(self):
        import src.http_server as http_module

        assert http_module._ACTIVE_CLIENT_ID.get() is None
        assert http_module._scoped_session("main") == "main"


class TestClientIdSessionScoping:
    """End-to-end: the X-Client-ID header namespaces the session an IDE asks
    for, so vscode and claude conversations named 'main' stay separate; a
    headerless client keeps the shared plain namespace."""

    def _run_request(self, monkeypatch, headers, payload_extra=None):
        import src.http_server as http_module

        seen = {}

        async def fake_run_agent_turn(**kwargs):
            seen["session"] = kwargs.get("session")
            return MetaLayer(generation="ok", finish_reason="final_answer")

        monkeypatch.setattr(http_module, "run_agent_turn", fake_run_agent_turn)
        payload = {
            "model": "unigrok-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "user": "main",
        }
        payload.update(payload_extra or {})
        with TestClient(create_app()) as client:
            res = client.post("/v1/chat/completions", json=payload, headers=headers)
        assert res.status_code == 200
        return seen["session"]

    def test_client_id_scopes_openai_facade_session(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        assert (
            self._run_request(monkeypatch, {"X-Client-ID": "vscode"})
            == "http%3Aanon:vscode:main"
        )

    def test_absent_header_keeps_shared_namespace(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        assert self._run_request(monkeypatch, {}) == "http%3Aanon:main"

    def test_x_caller_alone_does_not_scope_sessions(self, monkeypatch):
        """X-Caller attributes telemetry but must not fragment the session
        namespace — only the explicit X-Client-ID does that."""
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        assert (
            self._run_request(monkeypatch, {"X-Caller": "codex-cli"})
            == "http%3Aanon:main"
        )

    def test_static_keys_isolate_same_client_and_session(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.setenv("UNIGROK_API_KEYS", "first-key,second-key")
        first = self._run_request(
            monkeypatch,
            {"Authorization": "Bearer first-key", "X-Client-ID": "vscode"},
        )
        second = self._run_request(
            monkeypatch,
            {"Authorization": "Bearer second-key", "X-Client-ID": "vscode"},
        )
        assert first == "http%3Akey-1:vscode:main"
        assert second == "http%3Akey-2:vscode:main"
        assert first != second
