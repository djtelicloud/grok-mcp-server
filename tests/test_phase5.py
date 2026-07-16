# tests/test_phase5.py
# Phase 5 — Platform differentiators: deferred research jobs (JobManager +
# jobs table), agent mode="research" (multi-agent + inline citations), and
# MCP resources/prompts. Everything is SDK-mocked; no network calls.

import asyncio
import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jobs import JobManager, _job_timeout_sec, get_job_manager
from src.tools.chats import agent
from src.tools.research import (
    get_research_job,
    list_research_jobs,
    submit_research_job,
)
from src.utils import (
    DEFAULT_PLANNING_MODEL,
    AgentLoop,
    AgentLoopPolicy,
    GrokSessionStore,
    MetaLayer,
    _extract_citation_urls,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared SDK doubles
# ─────────────────────────────────────────────────────────────────────────────

class _FakeDeferChat:
    """Chat double for the deferred-job path: defer() returns one response or
    raises, recording the timeout it was bounded with."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.messages = []
        self.defer_timeout = None

    def append(self, message):
        self.messages.append(message)
        return self

    def defer(self, timeout=None):
        self.defer_timeout = timeout
        if self._error is not None:
            raise self._error
        return self._response


class _FakeJobClient:
    def __init__(self, chat):
        self._chat_obj = chat
        self.create_calls = []
        self.chat = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._chat_obj


class _FakeLoopChat:
    def __init__(self, responses):
        self._responses = list(responses)
        self.messages = []

    def append(self, message):
        self.messages.append(message)
        return self

    def sample(self):
        if not self._responses:
            raise AssertionError("no queued responses left on this chat")
        return self._responses.pop(0)


class _FakeLoopClient:
    def __init__(self, chats):
        self._chats = list(chats)
        self.create_calls = []
        self.chat = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        if not self._chats:
            raise AssertionError("no queued chats left")
        return self._chats.pop(0)


def _loop_response(content="", citations=None, inline_citations=None, tool_calls=None):
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls or []
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 5
    resp.usage.completion_tokens = 5
    resp.usage.reasoning_tokens = 0
    resp.cost_usd = 0.001
    resp.citations = citations or []
    resp.inline_citations = inline_citations or []
    resp.id = "resp-fake"
    resp.tool_outputs = []
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Jobs table (v5 migration) + store methods
# ─────────────────────────────────────────────────────────────────────────────

class TestJobsStore:
    @pytest.mark.asyncio
    async def test_jobs_migration_and_roundtrip(self, tmp_path):
        """The v5 migration creates the jobs table; create/update/get/list
        round-trip through the real SQLite store."""
        s = GrokSessionStore(db_path=tmp_path / "jobs.db")
        try:
            await s._ensure_initialized()
            async with s._conn.execute("PRAGMA user_version;") as cursor:
                row = await cursor.fetchone()
                assert row[0] >= 5
            async with s._conn.execute("SELECT name FROM sqlite_master WHERE type='index';") as cursor:
                indexes = {r[0] for r in await cursor.fetchall()}
                assert "idx_jobs_created_at" in indexes

            await s.create_job("job-a", prompt="research quantum stuff", model="grok-4.3")
            row = await s.get_job("job-a")
            assert row["status"] == "queued"
            assert row["model"] == "grok-4.3"
            assert "research quantum" in row["prompt"]
            assert row["created_at"] == row["updated_at"]

            await s.update_job("job-a", status="done", result="findings", cost=0.05)
            row = await s.get_job("job-a")
            assert row["status"] == "done"
            assert row["result"] == "findings"
            assert row["cost"] == pytest.approx(0.05)

            await s.create_job("job-b", prompt="second", model="grok-4.3")
            jobs = await s.list_jobs(limit=1)
            assert len(jobs) == 1
            assert await s.get_job("missing") is None
        finally:
            await s.close()


# ─────────────────────────────────────────────────────────────────────────────
# JobManager — deferred execution, error capture, staleness
# ─────────────────────────────────────────────────────────────────────────────

class TestJobManager:
    @pytest.mark.asyncio
    async def test_submit_runs_defer_and_persists_result(self, tmp_path):
        """submit() launches a background chat.defer() run: the row moves
        queued → running → done with the result, cost, and defaults to the
        planning model with server-side tools attached."""
        s = GrokSessionStore(db_path=tmp_path / "jm.db")
        chat = _FakeDeferChat(response=SimpleNamespace(content="deep findings", cost_usd=0.02))
        client = _FakeJobClient(chat)
        manager = JobManager(job_store=s)
        try:
            with patch("src.jobs.get_xai_client", return_value=client):
                submitted = await manager.submit("investigate the thing")
                assert submitted["status"] == "queued"
                assert submitted["model"] == DEFAULT_PLANNING_MODEL
                await manager.wait(submitted["job_id"])

            view = await manager.get(submitted["job_id"])
            assert view["status"] == "done"
            assert view["result"] == "deep findings"
            assert view["cost_usd"] == pytest.approx(0.02)
            # One create, on the resolved model, with the Tier-1 server-side
            # tool schemas attached so the deferred run can research upstream.
            assert len(client.create_calls) == 1
            assert client.create_calls[0]["model"] == DEFAULT_PLANNING_MODEL
            assert client.create_calls[0].get("tools")
            # defer was bounded by the job timeout.
            assert chat.defer_timeout is not None
            assert chat.defer_timeout.total_seconds() == pytest.approx(_job_timeout_sec())
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_defer_failure_persists_error(self, tmp_path):
        """A failing defer() never crashes the server: the exception is
        stored on the row as status=error."""
        s = GrokSessionStore(db_path=tmp_path / "jm_err.db")
        client = _FakeJobClient(_FakeDeferChat(error=RuntimeError("upstream exploded")))
        manager = JobManager(job_store=s)
        try:
            with patch("src.jobs.get_xai_client", return_value=client):
                submitted = await manager.submit("doomed job")
                await manager.wait(submitted["job_id"])

            view = await manager.get(submitted["job_id"])
            assert view["status"] == "error"
            assert "upstream exploded" in view["error"]
            assert "result" not in view
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_agent_count_forwarded_capability_gated(self, tmp_path):
        """agent_count rides chat.create when the installed SDK accepts it
        (verified true for xai_sdk 1.17)."""
        s = GrokSessionStore(db_path=tmp_path / "jm_ac.db")
        client = _FakeJobClient(_FakeDeferChat(response=SimpleNamespace(content="ok", cost_usd=None)))
        manager = JobManager(job_store=s)
        try:
            with patch("src.jobs.get_xai_client", return_value=client):
                submitted = await manager.submit("fan out", agent_count=16)
                await manager.wait(submitted["job_id"])
            assert client.create_calls[0].get("agent_count") == 16
            view = await manager.get(submitted["job_id"])
            assert view["status"] == "done"
            assert view["cost_usd"] == 0.0
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_concurrent_defer_calls_are_bounded(self, tmp_path, monkeypatch):
        """UNIGROK_MAX_CONCURRENT_JOBS gates the defer calls: excess jobs
        stay 'queued' (no timed thread pinned, no chat created) until a slot
        frees, so a submission burst can never exhaust the shared
        UNIGROK_MAX_TIMED_THREADS cap and starve other SDK calls."""
        monkeypatch.setenv("UNIGROK_MAX_CONCURRENT_JOBS", "1")
        s = GrokSessionStore(db_path=tmp_path / "jm_cap.db")
        release = threading.Event()
        create_calls = []

        class _BlockingDeferChat:
            def append(self, message):
                return self

            def defer(self, timeout=None):
                release.wait(timeout=10)
                return SimpleNamespace(content="ok", cost_usd=None)

        class _Client:
            def __init__(self):
                self.chat = SimpleNamespace(create=self._create)

            def _create(self, **kwargs):
                create_calls.append(kwargs)
                return _BlockingDeferChat()

        manager = JobManager(job_store=s)
        try:
            with patch("src.jobs.get_xai_client", return_value=_Client()):
                first = await manager.submit("job one")
                second = await manager.submit("job two")
                # Let the first job take the sole slot and enter its defer.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if (await manager.get(first["job_id"]))["status"] == "running":
                        break
                assert (await manager.get(first["job_id"]))["status"] == "running"
                assert (await manager.get(second["job_id"]))["status"] == "queued"
                assert len(create_calls) == 1  # second defer has not started
                release.set()
                await manager.wait(first["job_id"])
                await manager.wait(second["job_id"])
            assert (await manager.get(first["job_id"]))["status"] == "done"
            assert (await manager.get(second["job_id"]))["status"] == "done"
            assert len(create_calls) == 2
        finally:
            release.set()
            await s.close()

    @pytest.mark.asyncio
    async def test_job_result_and_error_are_redacted_and_bounded(self, tmp_path, monkeypatch):
        """Persisted results ride the same redaction/bounding as the prompt
        column (create_job): secrets echoed by the deferred answer never land
        raw in the jobs table, and oversized results are truncated at
        UNIGROK_JOB_RESULT_MAX_CHARS. Error strings are redacted too."""
        monkeypatch.setenv("UNIGROK_JOB_RESULT_MAX_CHARS", "1000")
        s = GrokSessionStore(db_path=tmp_path / "jm_redact.db")
        leaked = "the key is XAI_API_KEY=xai-supersecretvalue123 " + "x" * 2000
        client = _FakeJobClient(
            _FakeDeferChat(response=SimpleNamespace(content=leaked, cost_usd=None))
        )
        manager = JobManager(job_store=s)
        try:
            with patch("src.jobs.get_xai_client", return_value=client):
                submitted = await manager.submit("echo my config")
                await manager.wait(submitted["job_id"])
            row = await s.get_job(submitted["job_id"])
            assert "xai-supersecretvalue123" not in row["result"]
            assert "[REDACTED" in row["result"]
            assert len(row["result"]) < 1200
            assert "truncated" in row["result"]

            err_client = _FakeJobClient(
                _FakeDeferChat(error=RuntimeError("boom xai-anotherleakedkey99"))
            )
            with patch("src.jobs.get_xai_client", return_value=err_client):
                failed = await manager.submit("doomed")
                await manager.wait(failed["job_id"])
            row = await s.get_job(failed["job_id"])
            assert row["status"] == "error"
            assert "xai-anotherleakedkey99" not in row["result"]
        finally:
            await s.close()

    def test_stale_detection_views(self):
        """A queued/running row whose updated_at is older than the job
        timeout reads as 'stale' (the owning task did not survive a restart);
        fresh in-flight rows and terminal rows are untouched."""
        old = (datetime.now() - timedelta(seconds=_job_timeout_sec() + 60)).isoformat()
        fresh = datetime.now().isoformat()
        manager = JobManager()

        stale_row = {"id": "j1", "status": "running", "updated_at": old, "model": "m", "created_at": old}
        assert manager.describe(stale_row)["status"] == "stale"

        fresh_row = {"id": "j2", "status": "running", "updated_at": fresh, "model": "m", "created_at": fresh}
        assert manager.describe(fresh_row)["status"] == "running"

        done_row = {
            "id": "j3", "status": "done", "updated_at": old, "model": "m",
            "created_at": old, "result": "r", "cost": 0.1,
        }
        described = manager.describe(done_row)
        assert described["status"] == "done"
        assert described["result"] == "r"

    def test_live_task_not_false_stale_without_heartbeat(self):
        """Long defer / queue wait must not look stale while this process owns it."""
        from unittest.mock import MagicMock

        old = (datetime.now() - timedelta(seconds=_job_timeout_sec() + 60)).isoformat()
        manager = JobManager()
        live = MagicMock()
        live.done.return_value = False
        manager._tasks["live-job"] = live
        try:
            row = {
                "id": "live-job",
                "status": "running",
                "updated_at": old,
                "model": "m",
                "created_at": old,
            }
            assert manager.describe(row)["status"] == "running"
            live.done.return_value = True
            assert manager.describe(row)["status"] == "stale"
        finally:
            manager._tasks.pop("live-job", None)


# ─────────────────────────────────────────────────────────────────────────────
# Research MCP tools
# ─────────────────────────────────────────────────────────────────────────────

class TestResearchTools:
    @pytest.mark.asyncio
    async def test_submit_research_job_validates_inputs(self):
        res = await submit_research_job("")
        assert "Input Validation Error" in res["error"]
        res = await submit_research_job("valid prompt", agent_count=8)
        assert "agent_count must be either 4 or 16" in res["error"]

    @pytest.mark.asyncio
    async def test_get_research_job_reports_not_found(self):
        res = await get_research_job("no-such-job-id")
        assert res["status"] == "not_found"
        assert res["job_id"] == "no-such-job-id"

    @pytest.mark.asyncio
    async def test_list_research_jobs_shape(self, monkeypatch):
        manager = get_job_manager()
        monkeypatch.setattr(
            manager._store, "list_jobs",
            AsyncMock(return_value=[{
                "id": "j-x", "status": "done", "model": "m",
                "created_at": "t", "updated_at": "t", "result": "r", "cost": 0.0,
            }]),
        )
        res = await list_research_jobs(limit=5)
        assert res["count"] == 1
        assert res["jobs"][0]["job_id"] == "j-x"

    @pytest.mark.asyncio
    async def test_research_tools_registered_with_annotations(self):
        """submit is a mutating tool (no readOnlyHint); the gets are
        readOnly per the MCP spec annotations."""
        from src.server import mcp

        tools = {tool.name: tool for tool in await mcp.list_tools()}
        assert "submit_research_job" in tools
        submit_ann = tools["submit_research_job"].annotations
        assert submit_ann is None or submit_ann.readOnlyHint is not True
        for name in ("get_research_job", "list_research_jobs"):
            assert tools[name].annotations is not None, f"{name} missing annotations"
            assert tools[name].annotations.readOnlyHint is True, f"{name} not readOnlyHint"


# ─────────────────────────────────────────────────────────────────────────────
# agent mode="research" — multi-agent fan-out + inline citations
# ─────────────────────────────────────────────────────────────────────────────

class TestResearchMode:
    @pytest.mark.asyncio
    async def test_research_mode_maps_to_planning_multi_agent(self, monkeypatch):
        """mode='research' reaches the research capability class with
        agent_count=4 (default) and requests inline citations."""
        mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

        await agent(task="survey the field", mode="research")

        _, kwargs = mock_run.call_args
        assert kwargs["mode"] == "research"
        assert kwargs["thinking_mode"] is False
        assert kwargs["enable_agentic"] is True
        assert kwargs["agent_count"] == 4
        assert kwargs["include"] == ["inline_citations"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("env_value,expected", [("16", 16), ("7", 4), ("junk", 4)])
    async def test_research_agent_count_env(self, monkeypatch, env_value, expected):
        """UNIGROK_RESEARCH_AGENT_COUNT only accepts the SDK's 4|16; anything
        else falls back to 4."""
        monkeypatch.setenv("UNIGROK_RESEARCH_AGENT_COUNT", env_value)
        mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

        await agent(task="survey", mode="research")

        assert mock_run.call_args.kwargs["agent_count"] == expected

    @pytest.mark.asyncio
    async def test_non_research_modes_send_no_fanout_or_include(self, monkeypatch):
        mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

        await agent(task="plain question", mode="auto")

        kwargs = mock_run.call_args.kwargs
        assert kwargs["agent_count"] is None
        assert kwargs["include"] is None

    @pytest.mark.asyncio
    async def test_agent_surfaces_citations_only_when_present(self, monkeypatch):
        """citations appear in the structured return when the run collected
        sources; the key is absent otherwise (pinned return shape)."""
        with_sources = MetaLayer(generation="answer", citations=["https://a.example", "https://b.example"])
        mock_run = AsyncMock(return_value=with_sources)
        monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)
        res = await agent(task="cite things", mode="research")
        assert res.citations == [{"url": "https://a.example"}, {"url": "https://b.example"}]

        mock_run.return_value = MetaLayer(generation="answer")
        res = await agent(task="no sources", mode="auto")
        assert res.citations is None


# ─────────────────────────────────────────────────────────────────────────────
# include plumbing + citation collection through the AgentLoop
# ─────────────────────────────────────────────────────────────────────────────

class TestIncludeAndCitations:
    def test_extract_citation_urls_never_raises(self):
        """Unexpected response shapes (bare MagicMock — non-iterable
        attributes) yield an empty list instead of an exception."""
        assert _extract_citation_urls(MagicMock()) == []
        assert _extract_citation_urls(None) == []

    def test_extract_citation_urls_merges_plain_and_inline(self):
        inline = SimpleNamespace(
            web_citation=SimpleNamespace(url="https://inline.example"),
            x_citation=None,
            collections_citation=None,
        )
        resp = SimpleNamespace(
            citations=["https://a.example", "https://a.example"],
            inline_citations=[inline],
        )
        assert _extract_citation_urls(resp) == ["https://a.example", "https://inline.example"]

    @pytest.mark.asyncio
    async def test_agent_loop_forwards_include_and_collects_citations(self):
        """AgentLoop passes include= to chat.create (capability-gated, true on
        the installed SDK) and accumulates deduped citations onto the layer."""
        inline = SimpleNamespace(
            web_citation=SimpleNamespace(url="https://inline.example"),
            x_citation=None,
            collections_citation=None,
        )
        resp = _loop_response(
            content="final answer",
            citations=["https://a.example", "https://b.example"],
            inline_citations=[inline],
        )
        client = _FakeLoopClient([_FakeLoopChat([resp])])

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=2),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
            include=["inline_citations"],
        )
        with patch("src.utils.get_xai_client", return_value=client):
            layer = await loop.run("find sources")

        assert client.create_calls[0].get("include") == ["inline_citations"]
        assert layer.generation == "final answer"
        assert layer.citations == [
            "https://a.example", "https://b.example", "https://inline.example",
        ]

    @pytest.mark.asyncio
    async def test_run_agent_turn_forwards_agent_count_and_include(self, monkeypatch):
        from src.utils import run_agent_turn

        mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        await run_agent_turn(
            prompt="research it",
            agent_count=4,
            include=["inline_citations"],
        )

        kwargs = mock_orchestrate.call_args.kwargs
        assert kwargs["agent_count"] == 4
        assert kwargs["include"] == ["inline_citations"]


# ─────────────────────────────────────────────────────────────────────────────
# MCP resources & prompts
# ─────────────────────────────────────────────────────────────────────────────

class TestResourcesAndPrompts:
    @pytest.mark.asyncio
    async def test_resources_and_templates_registered(self):
        from src.server import mcp

        uris = {str(res.uri) for res in await mcp.list_resources()}
        assert {"grok://models", "grok://status", "grok://sessions"} <= uris

        templates = {tpl.uriTemplate for tpl in await mcp.list_resource_templates()}
        assert "grok://sessions/{name}" in templates
        assert "grok://jobs/{job_id}" in templates

    @pytest.mark.asyncio
    async def test_session_history_resource_reads_store(self):
        from src.server import mcp
        from src.utils import store

        await store.save_message("phase5-res-session", "user", "hello resources")
        try:
            contents = list(await mcp.read_resource("grok://sessions/phase5-res-session"))
            payload = json.loads(contents[0].content)
            assert any(msg.get("content") == "hello resources" for msg in payload)

            listing = list(await mcp.read_resource("grok://sessions"))
            sessions = json.loads(listing[0].content)
            assert any(s.get("session_name") == "phase5-res-session" for s in sessions)
        finally:
            await store.delete_session("phase5-res-session")

    @pytest.mark.asyncio
    async def test_job_resource_reports_not_found(self):
        from src.server import mcp

        contents = list(await mcp.read_resource("grok://jobs/definitely-missing"))
        payload = json.loads(contents[0].content)
        assert payload["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_prompts_registered_and_render(self):
        from src.server import mcp

        prompt_names = {p.name for p in await mcp.list_prompts()}
        assert {"research_topic", "fix_and_test"} <= prompt_names

        rendered = await mcp.get_prompt("research_topic", {"topic": "fusion energy"})
        text = rendered.messages[0].content.text
        assert "fusion energy" in text
        assert "web_search" in text

        rendered = await mcp.get_prompt("fix_and_test", {"path_or_description": "tests/test_api.py::test_login"})
        text = rendered.messages[0].content.text
        assert "tests/test_api.py::test_login" in text
        assert "run_local_tests" in text
