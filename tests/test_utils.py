"""
tests/test_utils.py

Unit tests for src/utils.py — covers all v2 agentic loop infrastructure:
  - PathResolver
  - GrokSessionStore (SQLite)
  - ToolObservation
  - AgentLoopPolicy
  - register_internal_tool / dispatch_internal_tool
  - AgentLoop._truncate and _dispatch_one (mocked)
  - GitContextCache
  - MetaLayer
  - History helpers (load/save/append)
  - Fast-path keyword heuristic (_REASONING_KEYWORDS)
  - usage_footer, encode helpers
"""

import asyncio
import concurrent.futures
import contextlib
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time

import aiosqlite
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure dummy key is set before any src import
os.environ.setdefault("XAI_API_KEY", "xai-test-dummy-key-for-unit-tests")

from evals.fakes import FakeChat, FakeClient, make_response
from src.utils import (
    AgentLoop,
    AgentLoopPolicy,
    GitContextCache,
    GrokInvocationContext,
    GrokSessionStore,
    MetaLayer,
    PathResolver,
    ToolObservation,
    _INTERNAL_TOOL_REGISTRY,
    _REASONING_KEYWORDS,
    AGENTIC_TOOLS_SCHEMA,
    ReflectionVerdict,
    append_and_save_history,
    dispatch_internal_tool,
    encode_image_to_base64,
    load_history,
    register_internal_tool,
    routing_reason_score,
    save_history,
    usage_footer,
)


# ─────────────────────────────────────────────────────────────────────────────
# PathResolver
# ─────────────────────────────────────────────────────────────────────────────

class TestPathResolver:
    def test_get_project_root_is_path(self):
        root = PathResolver.get_project_root()
        assert isinstance(root, Path)

    def test_get_project_root_contains_pyproject(self):
        root = PathResolver.get_project_root()
        assert (root / "pyproject.toml").exists(), "Project root should contain pyproject.toml"

    def test_get_logs_dir_creates_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_service_root", staticmethod(lambda: tmp_path))
        logs = PathResolver.get_logs_dir()
        assert logs.exists()
        assert logs.is_dir()

    def test_get_chats_dir_creates_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_service_root", staticmethod(lambda: tmp_path))
        chats = PathResolver.get_chats_dir()
        assert chats.exists()
        assert chats.is_dir()

    def test_get_grok_cli_path_returns_string(self):
        result = PathResolver.get_grok_cli_path()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_uv_path_returns_string(self):
        result = PathResolver.get_uv_path()
        assert isinstance(result, str)
        assert len(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# ToolObservation
# ─────────────────────────────────────────────────────────────────────────────

class TestToolObservation:
    def test_success_observation(self):
        obs = ToolObservation(tool_name="web_search", success=True, content="result text")
        assert obs.success is True
        assert obs.content == "result text"
        assert obs.elapsed == 0.0

    def test_failure_observation(self):
        obs = ToolObservation(tool_name="code_executor", success=False, content="timeout")
        assert obs.success is False

    def test_metadata_defaults_to_empty_dict(self):
        obs = ToolObservation(tool_name="x", success=True, content="y")
        assert obs.metadata == {}

    def test_error_content_preserved(self):
        """Failure observations expose their content directly (no orphaned method)."""
        obs = ToolObservation(tool_name="code_executor", success=False, content="timeout error")
        assert "timeout error" in obs.content

    def test_no_to_role_content_method(self):
        """to_role_content was deleted in Phase 1 — verify it is gone."""
        obs = ToolObservation(tool_name="x", success=True, content="y")
        assert not hasattr(obs, "to_role_content"), (
            "to_role_content() was deleted as dead code — it should not exist"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AgentLoopPolicy
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentLoopPolicy:
    def test_defaults(self):
        p = AgentLoopPolicy()
        assert p.max_depth == 8
        assert p.max_tool_calls_per_turn == 6
        assert p.per_tool_timeout_sec == 30.0
        assert p.global_budget_usd == 0.50
        assert p.max_obs_chars == 8000
        assert p.enable_parallel_dispatch is True

    def test_custom_values(self):
        p = AgentLoopPolicy(max_depth=3, global_budget_usd=0.10)
        assert p.max_depth == 3
        assert p.global_budget_usd == 0.10


# ─────────────────────────────────────────────────────────────────────────────
# Internal Tool Registry
# ─────────────────────────────────────────────────────────────────────────────

class TestInternalToolRegistry:
    def setup_method(self):
        # Snapshot existing keys so we don't pollute between tests
        self._original_keys = set(_INTERNAL_TOOL_REGISTRY.keys())

    def teardown_method(self):
        # Remove any test keys we added
        for k in list(_INTERNAL_TOOL_REGISTRY.keys()):
            if k not in self._original_keys:
                del _INTERNAL_TOOL_REGISTRY[k]

    def test_register_adds_to_registry(self):
        async def my_tool(x: str) -> str:
            return f"result:{x}"
        register_internal_tool("__test_tool__", my_tool)
        assert "__test_tool__" in _INTERNAL_TOOL_REGISTRY

    @pytest.mark.asyncio
    async def test_dispatch_calls_registered_tool(self):
        async def echo_tool(msg: str) -> str:
            return f"echo:{msg}"
        register_internal_tool("__echo__", echo_tool)
        obs = await dispatch_internal_tool("__echo__", {"msg": "hello"})
        assert obs.success is True
        assert obs.content == "echo:hello"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool_returns_failure(self):
        obs = await dispatch_internal_tool("__nonexistent_tool__", {})
        assert obs.success is False
        assert "not found" in obs.content

    @pytest.mark.asyncio
    async def test_dispatch_handles_tool_exception(self):
        async def broken_tool() -> str:
            raise ValueError("intentional error")
        register_internal_tool("__broken__", broken_tool)
        obs = await dispatch_internal_tool("__broken__", {})
        assert obs.success is False
        assert "ValueError" in obs.content
        assert "intentional error" in obs.content

    @pytest.mark.asyncio
    async def test_dispatch_timeout(self):
        async def slow_tool() -> str:
            await asyncio.sleep(10)
            return "done"
        register_internal_tool("__slow__", slow_tool)
        obs = await dispatch_internal_tool("__slow__", {}, timeout_sec=0.05)
        assert obs.success is False
        assert "timed out" in obs.content

    @pytest.mark.asyncio
    async def test_dispatch_records_elapsed_time(self):
        async def instant_tool() -> str:
            return "fast"
        register_internal_tool("__instant__", instant_tool)
        obs = await dispatch_internal_tool("__instant__", {})
        assert obs.elapsed >= 0.0

    @pytest.mark.asyncio
    async def test_dispatch_extracts_cost_metadata_from_tool_output(self):
        async def paid_tool() -> str:
            return "done\n\n---\n**Tokens:** 1 in / 1 out · **Cost:** $0.0420"

        register_internal_tool("__paid__", paid_tool)
        obs = await dispatch_internal_tool("__paid__", {})

        assert obs.success is True
        assert obs.metadata["cost_usd"] == 0.042


# ─────────────────────────────────────────────────────────────────────────────
# AGENTIC_TOOLS_SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentToolsSchema:
    def test_schema_is_list(self):
        assert isinstance(AGENTIC_TOOLS_SCHEMA, list)

    def test_schema_has_three_tools(self):
        # code_execution, web_search, x_search
        assert len(AGENTIC_TOOLS_SCHEMA) == 3

    def test_schema_items_are_not_none(self):
        for item in AGENTIC_TOOLS_SCHEMA:
            assert item is not None


# ─────────────────────────────────────────────────────────────────────────────
# AgentLoop._truncate
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentLoopTruncate:
    def _make_loop(self, max_obs_chars=100):
        policy = AgentLoopPolicy(max_obs_chars=max_obs_chars)
        return AgentLoop(policy=policy, dynamic_sys_prompt="", model="grok-4.3")

    def test_short_content_not_truncated(self):
        loop = self._make_loop(max_obs_chars=100)
        result = loop._truncate("hello", "tool")
        assert result == "hello"

    def test_long_content_is_truncated(self):
        loop = self._make_loop(max_obs_chars=10)
        result = loop._truncate("a" * 500, "my_tool")
        assert len(result) > 10  # has truncation note
        assert "truncated" in result
        assert "my_tool" in result

    def test_truncated_content_starts_with_original(self):
        loop = self._make_loop(max_obs_chars=5)
        result = loop._truncate("hello world truncated content", "tool")
        assert result.startswith("hello")

    def test_exact_limit_not_truncated(self):
        loop = self._make_loop(max_obs_chars=5)
        result = loop._truncate("hello", "tool")
        assert result == "hello"


# ─────────────────────────────────────────────────────────────────────────────
# AgentLoop._dispatch_one (mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentLoopDispatchOne:
    def _make_loop(self):
        policy = AgentLoopPolicy(per_tool_timeout_sec=5.0)
        return AgentLoop(policy=policy, dynamic_sys_prompt="", model="grok-4.3")

    def _make_tool_call(self, name: str, arguments: dict):
        tc = MagicMock()
        tc.function.name = name
        tc.function.arguments = json.dumps(arguments)
        return tc

    @pytest.mark.asyncio
    async def test_dispatch_one_calls_registry(self):
        async def my_fn(x: str) -> str:
            return f"got:{x}"
        register_internal_tool("__dispatch_test__", my_fn)
        loop = self._make_loop()
        tc = self._make_tool_call("__dispatch_test__", {"x": "val"})
        obs = await loop._dispatch_one(tc)
        assert obs.success is True
        assert "got:val" in obs.content
        # cleanup
        del _INTERNAL_TOOL_REGISTRY["__dispatch_test__"]

    @pytest.mark.asyncio
    async def test_dispatch_one_invalid_json_args(self):
        loop = self._make_loop()
        tc = MagicMock()
        tc.function.name = "some_tool"
        tc.function.arguments = "{invalid json!!!"
        obs = await loop._dispatch_one(tc)
        assert obs.success is False
        assert "parse" in obs.content.lower() or "error" in obs.content.lower()

    @pytest.mark.asyncio
    async def test_dispatch_one_unknown_tool(self):
        loop = self._make_loop()
        tc = self._make_tool_call("__nonexistent_xyz__", {})
        obs = await loop._dispatch_one(tc)
        assert obs.success is False


# ─────────────────────────────────────────────────────────────────────────────
# AgentLoop._dispatch_parallel (mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentLoopDispatchParallel:
    @pytest.mark.asyncio
    async def test_parallel_dispatch_runs_all(self):
        results = []

        async def tool_a(val: str) -> str:
            results.append("a")
            return "a"

        async def tool_b(val: str) -> str:
            results.append("b")
            return "b"

        register_internal_tool("__par_a__", tool_a)
        register_internal_tool("__par_b__", tool_b)

        policy = AgentLoopPolicy(enable_parallel_dispatch=True)
        loop = AgentLoop(policy=policy, dynamic_sys_prompt="", model="grok-4.3")

        def _make_tc(name):
            tc = MagicMock()
            tc.function.name = name
            tc.function.arguments = json.dumps({"val": "x"})
            return tc

        observations = await loop._dispatch_parallel([_make_tc("__par_a__"), _make_tc("__par_b__")])
        assert len(observations) == 2
        assert all(obs.success for obs in observations)
        assert set(results) == {"a", "b"}

        del _INTERNAL_TOOL_REGISTRY["__par_a__"]
        del _INTERNAL_TOOL_REGISTRY["__par_b__"]

    @pytest.mark.asyncio
    async def test_serial_dispatch_runs_in_order(self):
        """enable_parallel_dispatch=False must run tools sequentially."""
        order = []

        async def tool_first(val: str) -> str:
            order.append("first")
            return "first"

        async def tool_second(val: str) -> str:
            order.append("second")
            return "second"

        register_internal_tool("__ser_1__", tool_first)
        register_internal_tool("__ser_2__", tool_second)

        policy = AgentLoopPolicy(enable_parallel_dispatch=False)
        loop = AgentLoop(policy=policy, dynamic_sys_prompt="", model="grok-4.3")

        def _make_tc(name):
            tc = MagicMock()
            tc.function.name = name
            tc.function.arguments = json.dumps({"val": "x"})
            return tc

        observations = await loop._dispatch_parallel([_make_tc("__ser_1__"), _make_tc("__ser_2__")])
        assert len(observations) == 2
        assert order == ["first", "second"]  # strict ordering guaranteed

        del _INTERNAL_TOOL_REGISTRY["__ser_1__"]
        del _INTERNAL_TOOL_REGISTRY["__ser_2__"]

    @pytest.mark.asyncio
    async def test_parallel_dispatch_empty_list(self):
        policy = AgentLoopPolicy()
        loop = AgentLoop(policy=policy, dynamic_sys_prompt="", model="grok-4.3")
        result = await loop._dispatch_parallel([])
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# SelfOptimizationScore (tombstone)
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfOptimizationScoreDeleted:
    def test_self_optimization_score_is_deleted(self):
        """SelfOptimizationScore was an in-memory vanity score superseded by
        RoutingAdvisor's persisted telemetry — removed, must stay removed."""
        import src.utils as utils_module
        assert not hasattr(utils_module, "SelfOptimizationScore")


# ─────────────────────────────────────────────────────────────────────────────
# GitContextCache
# ─────────────────────────────────────────────────────────────────────────────

class TestGitContextCache:
    def test_cache_miss_returns_none(self):
        cache = GitContextCache(ttl=1.0)
        assert cache.get("missing_key") is None

    def test_cache_hit_returns_value(self):
        cache = GitContextCache(ttl=5.0)
        cache.set("key1", ("data", True))
        assert cache.get("key1") == ("data", True)

    def test_cache_expires_after_ttl(self):
        cache = GitContextCache(ttl=0.05)
        cache.set("expiring", "value")
        time.sleep(0.1)
        assert cache.get("expiring") is None

    def test_cache_overwrite(self):
        cache = GitContextCache(ttl=5.0)
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"

    def test_expired_entry_is_removed_on_get(self):
        """Expired entries are evicted, not just skipped — get_dynamic_context
        caches one entry per prompt hash, so lingering dead entries would leak
        memory proportional to unique-prompt volume."""
        cache = GitContextCache(ttl=0.05)
        cache.set("stale", "value")
        time.sleep(0.1)
        assert cache.get("stale") is None
        assert "stale" not in cache._cache

    def test_set_prunes_expired_entries(self):
        cache = GitContextCache(ttl=0.05)
        cache.set("old1", "v")
        cache.set("old2", "v")
        time.sleep(0.1)
        cache.set("fresh", "v")
        assert set(cache._cache) == {"fresh"}

    def test_max_entries_evicts_oldest_first(self):
        cache = GitContextCache(ttl=60.0, max_entries=3)
        for i in range(5):
            cache.set(f"k{i}", i)
            time.sleep(0.001)  # strictly ordered timestamps
        assert len(cache._cache) == 3
        assert cache.get("k0") is None
        assert cache.get("k1") is None
        assert [cache.get(f"k{i}") for i in (2, 3, 4)] == [2, 3, 4]

    def test_unique_prompt_keys_stay_bounded(self):
        """Regression (round-3 review): the prompt-keyed dynamic-context
        entries must never accumulate one permanent ~KB entry per unique
        prompt on a long-running server."""
        cache = GitContextCache(ttl=60.0, max_entries=8)
        for i in range(100):
            cache.set(f"dynamic_context:hash{i}", "ctx" * 200)
        assert len(cache._cache) <= 8

    def test_clear_prefix_drops_prompt_keyed_family(self):
        """Mutating tools invalidate the whole dynamic_context family — the
        legacy promptless key AND every prompt-hash variant."""
        cache = GitContextCache(ttl=60.0)
        cache.set("dynamic_context", "legacy")
        cache.set("dynamic_context:abc123", "hashed")
        cache.set("other_key", "keep")
        cache.clear_prefix("dynamic_context")
        assert cache.get("dynamic_context") is None
        assert cache.get("dynamic_context:abc123") is None
        assert cache.get("other_key") == "keep"

    def test_module_global_cache_is_bounded(self):
        """The module-global git_cache (get_dynamic_context's backing store)
        must carry a real bound."""
        from src.utils import git_cache
        assert getattr(git_cache, "max_entries", 0) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# MetaLayer
# ─────────────────────────────────────────────────────────────────────────────

class TestMetaLayer:
    def test_default_values(self):
        m = MetaLayer()
        assert m.plan == ""
        assert m.reasoning == ""
        assert m.generation == ""
        assert m.reflection == ""
        assert m.plane == "API"
        assert m.tokens == 0
        assert m.cost_usd == 0.0
        assert m.latency == 0.0
        assert m.fallback_occurred is False
        assert m.finish_reason == "unknown"
        assert m.tool_trace == []

    def test_custom_values(self):
        m = MetaLayer(generation="hello", plane="CLI", tokens=100, cost_usd=0.01)
        assert m.generation == "hello"
        assert m.plane == "CLI"
        assert m.tokens == 100
        assert m.cost_usd == 0.01


# ─────────────────────────────────────────────────────────────────────────────
# GrokSessionStore (uses temp SQLite DB via monkeypatching)
# ─────────────────────────────────────────────────────────────────────────────

class TestGrokSessionStore:
    @pytest.fixture
    async def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_chats_dir", classmethod(lambda cls: tmp_path))
        s = GrokSessionStore()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_get_nonexistent_session_returns_none(self, store):
        result = await store.get_session("ghost_session")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_get_session(self, store):
        await store.save_session("sess1", api_thread_id="thread_123", model="grok-4.3")
        result = await store.get_session("sess1")
        assert result is not None
        assert result["api_thread_id"] == "thread_123"
        assert result["model"] == "grok-4.3"

    @pytest.mark.asyncio
    async def test_update_existing_session(self, store):
        await store.save_session("sess2", model="grok-build-0.1")
        await store.save_session("sess2", model="grok-4.3")
        result = await store.get_session("sess2")
        assert result["model"] == "grok-4.3"

    @pytest.mark.asyncio
    async def test_delete_session(self, store):
        await store.save_session("sess3", model="grok-4.3")
        await store.delete_session("sess3")
        result = await store.get_session("sess3")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sessions_returns_all(self, store):
        await store.save_session("alpha", model="grok-4.3")
        await store.save_session("beta", model="grok-build-0.1")
        sessions = await store.list_sessions()
        names = {s["session_name"] for s in sessions}
        assert "alpha" in names
        assert "beta" in names

    @pytest.mark.asyncio
    async def test_list_sessions_ordered_by_last_active(self, store):
        await store.save_session("first", model="grok-4.3")
        await asyncio.sleep(0.01)
        await store.save_session("second", model="grok-4.3")
        sessions = await store.list_sessions()
        names = [s["session_name"] for s in sessions]
        assert names.index("second") < names.index("first")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("db_path", [":memory:", Path(":memory:")])
    async def test_memory_db_initializes_without_parent_directory(self, db_path):
        s = GrokSessionStore(db_path=db_path)
        try:
            await s.save_session("memory-sess", model="grok-4.3")
            result = await s.get_session("memory-sess")
        finally:
            await s.close()

        assert result is not None
        assert result["model"] == "grok-4.3"

    @pytest.mark.asyncio
    async def test_relative_db_path_initializes_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s = GrokSessionStore(db_path="state/relative.db")
        try:
            await s.save_session("relative-sess", model="grok-4.3")
            result = await s.get_session("relative-sess")
        finally:
            await s.close()

        assert result is not None
        assert (tmp_path / "state" / "relative.db").exists()


# ─────────────────────────────────────────────────────────────────────────────
# History helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryHelpers:
    @pytest.fixture
    async def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_chats_dir", classmethod(lambda cls: tmp_path))
        s = GrokSessionStore()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_load_history_missing_returns_empty(self, store):
        result = await load_history("nonexistent_session_xyz", store)
        assert result == []

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, store):
        history = [{"role": "user", "content": "hi", "time": "now"}]
        await save_history("test_sess", history, store)
        loaded = await load_history("test_sess", store)
        # Clear timestamps to compare content
        for m in loaded:
            if "time" in m:
                del m["time"]
        for m in history:
            if "time" in m:
                del m["time"]
        assert loaded == history

    @pytest.mark.asyncio
    async def test_append_and_save_history(self, store):
        history = []
        await append_and_save_history("test_sess2", history, "Hello", "World", store)
        loaded = await load_history("test_sess2", store)
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "Hello"
        assert loaded[1]["role"] == "assistant"
        assert loaded[1]["content"] == "World"

    @pytest.mark.asyncio
    async def test_append_multiple_turns(self, store):
        history = []
        await append_and_save_history("test_sess3", history, "Turn 1 Q", "Turn 1 A", store)
        history = await load_history("test_sess3", store)
        await append_and_save_history("test_sess3", history, "Turn 2 Q", "Turn 2 A", store)
        loaded = await load_history("test_sess3", store)
        assert len(loaded) == 4


# ─────────────────────────────────────────────────────────────────────────────
# usage_footer
# ─────────────────────────────────────────────────────────────────────────────

class TestUsageFooter:
    def test_empty_when_no_usage(self):
        result = usage_footer()
        assert result == ""

    def test_footer_with_mock_response(self):
        mock_resp = MagicMock()
        mock_resp.usage = MagicMock()
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50
        mock_resp.usage.reasoning_tokens = 0
        mock_resp.cost_usd = 0.0025
        result = usage_footer(mock_resp)
        assert "100" in result
        assert "50" in result
        assert "$0.0025" in result

    def test_reasoning_tokens_shown_when_nonzero(self):
        mock_resp = MagicMock()
        mock_resp.usage = MagicMock()
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50
        mock_resp.usage.reasoning_tokens = 30
        mock_resp.cost_usd = None
        result = usage_footer(mock_resp)
        assert "30" in result
        assert "reasoning" in result


# ─────────────────────────────────────────────────────────────────────────────
# encode_image_to_base64
# ─────────────────────────────────────────────────────────────────────────────

class TestEncodeImage:
    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            encode_image_to_base64("/nonexistent/path/image.png")

    def test_encodes_real_file(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        result = encode_image_to_base64(str(img))
        assert isinstance(result, str)
        assert len(result) > 0
        # Valid base64 characters only
        import base64
        base64.b64decode(result)  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# _REASONING_KEYWORDS (fast-path heuristic)
# ─────────────────────────────────────────────────────────────────────────────

class TestReasoningKeywords:
    def test_keywords_is_set(self):
        assert isinstance(_REASONING_KEYWORDS, set)

    def test_has_expected_keywords(self):
        for kw in ["architect", "design", "strategy", "research", "analyze"]:
            assert kw in _REASONING_KEYWORDS

    def test_simple_coding_prompts_dont_match(self):
        simple_prompts = [
            "fix the syntax error",
            "add a print statement",
            "rename this variable",
            "format this JSON",
        ]
        for prompt in simple_prompts:
            matched = any(kw in prompt.lower() for kw in _REASONING_KEYWORDS)
            assert not matched, f"Simple prompt '{prompt}' incorrectly matched reasoning keywords"

    def test_complex_prompts_do_match(self):
        complex_prompts = [
            "architect a distributed system",
            "research the best approach",
            "analyze the performance tradeoffs",
            "design a patent-worthy algorithm",
            "audit the intelligence of this gateway harness",
        ]
        for prompt in complex_prompts:
            matched = any(kw in prompt.lower() for kw in _REASONING_KEYWORDS)
            assert matched, f"Complex prompt '{prompt}' should have matched reasoning keywords"

    def test_mode_auto_heuristic_selects_model_not_agent_access(self):
        """Default-agentic routing: mode='auto' always runs AgentLoop; the
        keyword heuristic is repurposed to SELECT THE MODEL only. Simple
        prompts must map to the coding model, reasoning-scored prompts to the
        planning model (see TestDefaultAgenticRouting for the routing itself).
        """
        simple_prompt = "fix the syntax error on line 5"
        assert routing_reason_score(simple_prompt) < 2, (
            f"Simple prompt escalated to the planning model unexpectedly: '{simple_prompt}'"
        )

        complex_prompt = "architect a microservices system for this use case"
        assert routing_reason_score(complex_prompt) >= 2, (
            f"Complex prompt failed to select the planning model: '{complex_prompt}'"
        )

    def test_scored_router_avoids_broad_keyword_false_positive(self):
        assert routing_reason_score("update the product label text") < 2

    def test_scored_router_keeps_high_signal_prompts(self):
        prompt = "audit the intelligence of this gateway harness and make a plan"
        assert routing_reason_score(prompt) >= 2

    def test_classify_intent_is_deleted(self):
        """Phase 1D: classify_intent() was dead code and has been removed."""
        import src.utils as utils_module
        assert not hasattr(utils_module, "classify_intent"), (
            "classify_intent() was deleted in Phase 1 refactor — it should not exist"
        )

    def test_zero_caller_helpers_are_deleted(self):
        """Round 3 dead-code sweep: helpers with zero non-test callers were
        removed and must stay removed. Production routes score prompts via
        routing_reason_score directly (threshold >= 2 inlined in orchestrate)."""
        import src.utils as utils_module
        import src.http_server as http_module
        assert not hasattr(utils_module, "should_use_reasoning")
        assert not hasattr(utils_module, "build_params")
        assert not hasattr(http_module, "_message_text")


class TestReflectionVerdict:
    """Phase 1: the reviewer verdict is schema-enforced via
    chat.parse(ReflectionVerdict); the string-scanning parser is gone."""

    def test_parses_fail_verdict(self):
        verdict = ReflectionVerdict.model_validate_json(
            '{"status":"fail","issues":["boom"],"next_action":"replan"}'
        )
        assert verdict.status == "fail"
        assert verdict.issues == ["boom"]
        assert verdict.next_action == "replan"

    def test_parses_pass_verdict_with_defaults(self):
        verdict = ReflectionVerdict.model_validate_json('{"status":"pass"}')
        assert verdict.status == "pass"
        assert verdict.issues == []
        assert verdict.next_action == ""

    def test_schema_rejects_unknown_status(self):
        with pytest.raises(Exception):
            ReflectionVerdict.model_validate_json(
                '{"status":"maybe","issues":[],"next_action":""}'
            )

    def test_string_scanning_helpers_are_deleted(self):
        """The brittle text layer is retired: no keyword scanning, no regex
        JSON extraction, no local-verification heuristic."""
        import src.utils as utils_module

        for name in (
            "_reflection_indicates_failure",
            "_extract_json_object",
            "_should_attempt_local_verification",
        ):
            assert not hasattr(utils_module, name), (
                f"{name} was deleted in the structured-reflection refactor"
            )


# ─────────────────────────────────────────────────────────────────────────────
# GrokInvocationContext
# ─────────────────────────────────────────────────────────────────────────────

class TestGrokInvocationContext:
    @pytest.mark.asyncio
    async def test_context_manager_tracks_elapsed(self):
        import logging
        logger = logging.getLogger("test")
        async with GrokInvocationContext("grok-4.3", logger, append_signature=False) as ctx:
            await asyncio.sleep(0.01)
        assert ctx.elapsed >= 0.01

    @pytest.mark.asyncio
    async def test_format_output_no_signature(self):
        import logging
        logger = logging.getLogger("test")
        async with GrokInvocationContext("grok-4.3", logger, append_signature=False) as ctx:
            pass
        result = ctx.format_output("hello world")
        assert "hello world" in result
        assert "Used:" not in result  # No signature

    @pytest.mark.asyncio
    async def test_format_output_signature_off_by_default(self):
        """Quick win: the branded footer defaults OFF — downstream agents were
        ingesting it as content. Cost/usage stays available via MetaLayer."""
        import logging
        logger = logging.getLogger("test")
        async with GrokInvocationContext("grok-4.3", logger, append_signature=True) as ctx:
            pass
        result = ctx.format_output("content here")
        assert result == "content here"
        assert "Used:" not in result
        assert "Cooperative" not in result

    @pytest.mark.asyncio
    async def test_format_output_with_signature_opt_in(self, monkeypatch):
        """GROK_MCP_ENABLE_SIGNATURE=1 explicitly opts the footer back in."""
        import logging
        monkeypatch.setenv("GROK_MCP_ENABLE_SIGNATURE", "1")
        logger = logging.getLogger("test")
        async with GrokInvocationContext("grok-4.3", logger, append_signature=True) as ctx:
            pass
        result = ctx.format_output("content here")
        assert "content here" in result
        assert "Used:" in result
        assert "grok-4.3" in result

    @pytest.mark.asyncio
    async def test_format_output_suppress_wins_over_enable(self, monkeypatch):
        """The legacy suppress env still wins even when enable is set."""
        import logging
        monkeypatch.setenv("GROK_MCP_ENABLE_SIGNATURE", "1")
        monkeypatch.setenv("GROK_MCP_SUPPRESS_SIGNATURE", "1")
        logger = logging.getLogger("test")
        async with GrokInvocationContext("grok-4.3", logger, append_signature=True) as ctx:
            pass
        result = ctx.format_output("content here")
        assert "Used:" not in result


# ─────────────────────────────────────────────────────────────────────────────
# Registered Tier 2 tools in server.py
# ─────────────────────────────────────────────────────────────────────────────

class TestTier2ToolsRegistered:
    """Verify that server.py registers all expected Tier 2 raw tools."""

    def test_expected_tools_are_registered(self):
        # Import server to trigger modular tool registration.
        import src.server  # noqa: F401

        expected = {
            "generate_image",
            "upload_file",
            "get_file_content",
            "read_local_file",
            "list_project_files",
            "get_session_history",
            "git_status",
            "git_diff",
            "git_log",
            "git_show",
            "git_current_branch",
            "git_apply_patch",
            "git_commit",
            "git_create_branch",
            "run_local_tests",
        }
        registered = set(_INTERNAL_TOOL_REGISTRY.keys())
        assert expected.issubset(registered), (
            f"Missing tools: {expected - registered}"
        )

    @pytest.mark.asyncio
    async def test_list_project_files_returns_string(self, tmp_path, monkeypatch):
        """raw_list_project_files should run and return a non-empty string."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("pass\n", encoding="utf-8")
        monkeypatch.setattr(
            PathResolver,
            "get_workspace_root",
            staticmethod(lambda: tmp_path),
        )

        obs = await dispatch_internal_tool("list_project_files", {})

        assert obs.success is True
        assert "`src/app.py`" in obs.content

    @pytest.mark.asyncio
    async def test_list_project_files_skips_nested_worktrees(self, tmp_path, monkeypatch):
        """Nested repositories must not consume the bounded project listing."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass\n")
        nested = tmp_path / ".claude" / "worktrees" / "task"
        nested.mkdir(parents=True)
        (nested / ".git").write_text("gitdir: /tmp/example\n")
        for index in range(250):
            (nested / f"file-{index:03}.py").write_text("pass\n")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

        obs = await dispatch_internal_tool("list_project_files", {})

        assert obs.success is True
        assert "`src/app.py`" in obs.content
        assert ".claude/worktrees/task" not in obs.content

    @pytest.mark.asyncio
    async def test_read_local_file_blocks_traversal(self):
        """raw_read_local_file must block paths escaping the project root."""
        obs = await dispatch_internal_tool(
            "read_local_file", {"file_path": "../../etc/passwd"}
        )
        assert obs.success is True  # dispatch succeeds (no exception)
        assert "[BLOCKED]" in obs.content

    @pytest.mark.asyncio
    async def test_read_local_file_reads_real_file(self):
        """raw_read_local_file should read pyproject.toml successfully."""
        obs = await dispatch_internal_tool(
            "read_local_file", {"file_path": "pyproject.toml"}
        )
        assert obs.success is True
        assert "mcp-grok" in obs.content

    @pytest.mark.asyncio
    async def test_read_local_file_blocks_ignored_private_file(self, tmp_path, monkeypatch):
        """raw_read_local_file must not read ignored/private files under the root."""
        (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        (tmp_path / ".env").write_text("SECRET=leak", encoding="utf-8")
        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))

        obs = await dispatch_internal_tool("read_local_file", {"file_path": ".env"})

        assert obs.success is True
        assert "[BLOCKED]" in obs.content
        assert "SECRET=leak" not in obs.content

    @pytest.mark.asyncio
    async def test_get_session_history_missing_session(self):
        """raw_get_session_history returns graceful message for unknown session."""
        obs = await dispatch_internal_tool(
            "get_session_history", {"session": "__nonexistent_xyz_session__"}
        )
        assert obs.success is True
        assert "No history" in obs.content

    @pytest.mark.asyncio
    async def test_run_local_tests_uses_validated_pytest_target(self, tmp_path, monkeypatch):
        from src.tools import system as system_tools

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(system_tools.shutil, "which", lambda name: None)

        captured = {}

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return b"1 passed\n", b""

            def kill(self):
                return None

            async def wait(self):
                return 0

        async def fake_create_subprocess_exec(*cmd, cwd, stdout, stderr):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        result = await system_tools.run_local_tests("tests/test_sample.py")

        assert "Local tests passed" in result
        assert captured["cwd"] == str(tmp_path)
        assert captured["cmd"][-1] == "tests/test_sample.py"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — AgentLoop Failure-Path Tests (SDK mocked, no network calls)
# These test the ReAct loop control flow under the conditions that will
# actually occur in production: API errors, budget exhaustion, depth limits, etc.
# ─────────────────────────────────────────────────────────────────────────────

# Fake SDK response builder now lives in evals/fakes.py, shared with the
# offline eval harness (cassette replay drives the same AgentLoop paths these
# tests exercise). Kept under the old local name for the ~60 call sites.
_make_response = make_response


class TestAgentLoopFailurePaths:
    """All tests mock xai_sdk.Client so no real API calls are made.

    Because AgentLoop.run() does 'from xai_sdk import Client' inside the function,
    we patch 'xai_sdk.Client' (the source) rather than 'src.utils.Client'.
    We also patch asyncio.to_thread to run callables synchronously in tests,
    so the mock Client is actually called on the test event loop.
    """

    def _make_loop(self, policy: AgentLoopPolicy = None):
        policy = policy or AgentLoopPolicy()
        return AgentLoop(policy=policy, dynamic_sys_prompt="You are helpful.", model="grok-4.3")

    @staticmethod
    async def _sync_to_thread(fn, *args, **kwargs):
        """Replacement for asyncio.to_thread that runs fn() synchronously."""
        return fn(*args, **kwargs)

    # ── Test 1: sample() raises — verify it propagates after retries ──────────
    @pytest.mark.asyncio
    async def test_sample_exception_propagates_after_retries(self):
        """If chat.sample() keeps failing, the exception must propagate up
        so orchestrate() can catch it and fall back to the fast path.
        Retries must be exhausted before raising (not raise on first failure).
        """
        call_count = 0

        def _boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("xAI API unreachable")

        loop = self._make_loop(AgentLoopPolicy(max_depth=1))

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _boom
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            # Retries introduce asyncio.sleep — patch it to be instant
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ConnectionError, match="xAI API unreachable"):
                    await loop.run("test prompt")

        # _MAX_SAMPLE_RETRIES = 2 → 3 total attempts (1 + 2 retries)
        assert call_count == 3, f"Expected 3 attempts (1 + 2 retries), got {call_count}"

    @pytest.mark.asyncio
    async def test_sample_retry_emits_one_row_per_physical_api_call(self):
        attempts = []

        def record_attempt(**attempt):
            attempts.append(attempt)

        response = _make_response(
            content="recovered answer", tool_calls=[], cost_usd=0.002
        )
        call_count = 0

        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient xAI failure")
            return response

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="You are helpful.",
            model="grok-4.3",
            attempt_recorder=record_attempt,
        )
        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        with patch("xai_sdk.Client", return_value=mock_client), patch(
            "asyncio.sleep", new_callable=AsyncMock
        ):
            layer = await loop.run("test prompt")

        assert layer.generation == "recovered answer"
        assert call_count == 2
        assert len(attempts) == call_count
        assert [attempt["outcome"] for attempt in attempts] == [
            "error",
            "completed",
        ]
        assert attempts[0].get("cost_usd") is None
        assert attempts[1]["cost_usd"] == pytest.approx(0.002)

    @pytest.mark.asyncio
    async def test_missing_api_usage_stays_unavailable_in_agentic_receipt(self):
        from src.utils import orchestrate

        class MissingUsageLoop:
            def __init__(self, **kwargs):
                self.recorder = kwargs["attempt_recorder"]

            async def run(self, *args, **kwargs):
                self.recorder(
                    plane="API",
                    model="grok-build-0.1",
                    outcome="completed",
                    purpose="agentic:depth 1",
                    tokens=None,
                    cost_usd=None,
                )
                return MetaLayer(
                    generation="answer without usage metadata",
                    finish_reason="final_answer",
                    plane="API",
                    tokens=0,
                    cost_usd=0.0,
                )

        selection = (
            "grok-build-0.1",
            "auto",
            {
                "route_class": "coding",
                "resolved_model": "grok-build-0.1",
                "catalog": {"source": "xai_api_live", "fallback": False},
            },
            False,
        )
        store = MagicMock()
        store.get_similar_task_memories = AsyncMock(return_value=[])
        store.save_telemetry = AsyncMock()
        store.save_task_memory = AsyncMock()
        with patch(
            "src.utils._select_routing_model", new=AsyncMock(return_value=selection)
        ), patch("src.utils.AgentLoop", MissingUsageLoop):
            layer = await orchestrate(
                prompt="answer this",
                store=store,
                enable_agentic=True,
                requested_plane="api",
                fallback_policy="same_plane",
            )

        attempt = layer.routing_receipt["attempts"][0]
        assert attempt["cost_usd"] is None
        assert attempt["billing_source"] == "unavailable"
        saved = store.save_telemetry.await_args.kwargs
        assert saved["billing_source"] == "unknown"
        assert saved["token_kind"] == "unavailable"

    # ── Test 2: budget fires mid-loop — partial result is preserved ───────────
    @pytest.mark.asyncio
    async def test_budget_enforcement_fires_and_preserves_partial_content(self):
        """When cumulative cost >= global_budget_usd, the loop must stop and
        preserve whatever content the last response had as layer.generation.
        """
        resp1 = _make_response(content="partial reasoning", tool_calls=[], cost_usd=0.40)
        resp2 = _make_response(content="final answer", tool_calls=[], cost_usd=0.40)

        call_count = 0

        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return [resp1, resp2][min(call_count - 1, 1)]

        policy = AgentLoopPolicy(max_depth=5, global_budget_usd=0.50)
        loop = self._make_loop(policy)

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            layer = await loop.run("expensive prompt")

        # Loop stopped before depth 5
        assert call_count <= 2
        assert layer.generation in ("partial reasoning", "final answer")
        assert layer.cost_usd >= 0.40

    # ── Test 3: max_depth exhaustion — for/else clause gives a generation ─────
    @pytest.mark.asyncio
    async def test_max_depth_exhaustion_produces_generation(self):
        """When the model keeps requesting tool calls for every turn, the loop
        must exhaust max_depth cleanly and still return some generation content.
        """
        def _make_tool_call():
            tc = MagicMock()
            tc.function.name = "__nonexistent_tool__"
            tc.function.arguments = "{}"
            return tc

        resp = _make_response(
            content="still thinking...",
            tool_calls=[_make_tool_call()],
            cost_usd=0.001,
        )

        policy = AgentLoopPolicy(max_depth=3)
        loop = self._make_loop(policy)

        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            layer = await loop.run("infinite tool loop")

        assert layer.generation, "layer.generation must not be empty after max_depth"
        assert layer.finish_reason == "depth_exhausted"
        assert mock_chat.sample.call_count == 3

    # ── Test 4: mixed parallel success/failure — both observations captured ───
    @pytest.mark.asyncio
    async def test_mixed_parallel_dispatch_captures_both_observations(self):
        """When parallel dispatch has one success and one failure, both
        ToolObservations must be present in layer.reflection.
        """
        async def good_tool(val: str = "") -> str:
            return "good"

        async def bad_tool(val: str = "") -> str:
            raise ValueError("bad tool failure")

        register_internal_tool("__mixed_good__", good_tool)
        register_internal_tool("__mixed_bad__", bad_tool)

        def _make_tc(name):
            tc = MagicMock()
            tc.function.name = name
            tc.function.arguments = "{}"
            return tc

        # Turn 1: two tool calls. Turn 2: final answer, no tools
        resp_with_tools = _make_response(
            content="",
            tool_calls=[_make_tc("__mixed_good__"), _make_tc("__mixed_bad__")],
            cost_usd=0.001,
        )
        resp_final = _make_response(content="done", tool_calls=[], cost_usd=0.001)

        call_count = 0
        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return resp_with_tools if call_count == 1 else resp_final

        policy = AgentLoopPolicy(max_depth=5)
        loop = self._make_loop(policy)

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            layer = await loop.run("mixed tools prompt")

        # Cleanup internal tools
        del _INTERNAL_TOOL_REGISTRY["__mixed_good__"]
        del _INTERNAL_TOOL_REGISTRY["__mixed_bad__"]

        # Both tool names should appear in reflection
        assert "__mixed_good__" in layer.reflection
        assert "__mixed_bad__" in layer.reflection
        # Final generation must be the clean answer
        assert layer.generation == "done"

    @pytest.mark.asyncio
    async def test_tool_results_include_tool_call_id(self):
        """Tool observations must be associated with the SDK tool call id."""
        async def id_tool() -> str:
            return "tool observation"

        register_internal_tool("__id_tool__", id_tool)

        tc = MagicMock()
        tc.id = "call-123"
        tc.function.name = "__id_tool__"
        tc.function.arguments = "{}"

        resp_with_tool = _make_response(content="", tool_calls=[tc], cost_usd=0.001)
        resp_final = _make_response(content="done", tool_calls=[], cost_usd=0.001)

        call_count = 0
        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return resp_with_tool if call_count == 1 else resp_final

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_tool_result = MagicMock(side_effect=lambda result, tool_call_id=None: f"tool:{tool_call_id}:{result}")

        try:
            with patch("xai_sdk.Client", return_value=mock_client), \
                 patch("asyncio.to_thread", new=self._sync_to_thread), \
                 patch("xai_sdk.chat.tool_result", new=mock_tool_result):
                layer = await self._make_loop(AgentLoopPolicy(max_depth=3)).run("use tool")
        finally:
            _INTERNAL_TOOL_REGISTRY.pop("__id_tool__", None)

        assert layer.generation == "done"
        mock_tool_result.assert_called_once_with("tool observation", tool_call_id="call-123")

    # ── Test 5: empty AGENTIC_TOOLS_SCHEMA — loop still runs gracefully ───────
    @pytest.mark.asyncio
    async def test_empty_tools_schema_loop_runs_without_tools(self):
        """If AGENTIC_TOOLS_SCHEMA is empty (xai_sdk tools unavailable at import),
        AgentLoop must still run and call chat.sample() without the tools argument.
        """
        resp = _make_response(content="no-tools answer", tool_calls=[], cost_usd=0.001)

        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        loop = self._make_loop()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread), \
             patch("src.utils.AGENTIC_TOOLS_SCHEMA", []), \
             patch("src.utils._build_custom_tools", return_value=[]):
            layer = await loop.run("simple prompt")

        assert layer.generation == "no-tools answer"
        # sample must have been called with no 'tools' kwarg when schema is empty
        call_kwargs = mock_chat.sample.call_args
        if call_kwargs and call_kwargs.kwargs:
            assert call_kwargs.kwargs.get("tools") in (None, [])

    @pytest.mark.asyncio
    async def test_agentloop_attaches_tools_at_chat_create(self):
        """xAI SDK tools must be attached at chat.create(), not chat.sample()."""
        resp = _make_response(content="tool-ready answer", tool_calls=[], cost_usd=0.001)

        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        loop = self._make_loop(AgentLoopPolicy(max_depth=1))

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread), \
             patch("src.utils.AGENTIC_TOOLS_SCHEMA", ["server-tool"]), \
             patch("src.utils._build_custom_tools", return_value=["local-tool"]):
            layer = await loop.run("simple prompt")

        mock_client.chat.create.assert_called_once()
        assert mock_client.chat.create.call_args.kwargs["tools"] == ["server-tool", "local-tool"]
        mock_chat.sample.assert_called_once_with()
        assert layer.generation == "tool-ready answer"

    # ── Test 6: cost accumulates correctly across multiple turns ──────────────
    @pytest.mark.asyncio
    async def test_cost_accumulates_across_turns(self):
        """Total cost must be the sum of all per-turn costs, not just the last."""
        costs = [0.005, 0.008, 0.003]

        def _make_tc():
            tc = MagicMock()
            tc.function.name = "__nonexistent_tool__"
            tc.function.arguments = "{}"
            return tc

        resps = [
            _make_response(content="", tool_calls=[_make_tc()], cost_usd=c)
            for c in costs[:2]
        ]
        resps.append(_make_response(content="final", tool_calls=[], cost_usd=costs[2]))

        call_count = 0
        def _sample(*args, **kwargs):
            nonlocal call_count
            r = resps[min(call_count, len(resps) - 1)]
            call_count += 1
            return r

        policy = AgentLoopPolicy(max_depth=5, global_budget_usd=1.0)
        loop = self._make_loop(policy)

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            layer = await loop.run("multi-turn prompt")

        expected_total = sum(costs[:call_count])
        assert abs(layer.cost_usd - expected_total) < 1e-9, (
            f"Cost {layer.cost_usd} != expected {expected_total}"
        )

    @pytest.mark.asyncio
    async def test_local_tool_cost_counts_against_agentloop_budget(self):
        """Nested paid local tools must stop the loop after dispatch: no more
        tool turns, only the single budget-synthesis sample. When that final
        sample yields no text, the fallback budget message stands.
        """
        async def paid_tool() -> str:
            return "image generated\n\n---\n**Cost:** $0.0200"

        register_internal_tool("__paid_local_tool__", paid_tool)

        tc = MagicMock()
        tc.function.name = "__paid_local_tool__"
        tc.function.arguments = "{}"
        resp = _make_response(content="", tool_calls=[tc], cost_usd=0.0)

        policy = AgentLoopPolicy(max_depth=5, global_budget_usd=0.01)
        loop = self._make_loop(policy)

        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        try:
            with patch("xai_sdk.Client", return_value=mock_client), \
                 patch("asyncio.to_thread", new=self._sync_to_thread), \
                 patch("src.utils.AGENTIC_TOOLS_SCHEMA", []), \
                 patch("src.utils._build_custom_tools", return_value=[]):
                layer = await loop.run("generate an image")
        finally:
            _INTERNAL_TOOL_REGISTRY.pop("__paid_local_tool__", None)

        # One tool turn + one budget-synthesis sample; the tool is never
        # dispatched a second time.
        assert mock_chat.sample.call_count == 2
        assert layer.cost_usd == 0.02
        assert "Budget ceiling reached" in layer.generation
        assert layer.finish_reason == "budget_exhausted"
        assert "__paid_local_tool__" in layer.reflection

    # ── Test 7: enable_agentic=False bypasses AgentLoop entirely ─────────────
    @pytest.mark.asyncio
    async def test_enable_agentic_false_bypasses_loop(self):
        """orchestrate(enable_agentic=False) must never construct an AgentLoop."""
        from src.utils import orchestrate

        with patch("src.utils.AgentLoop") as mock_loop_cls, \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("fast path result", 50, 0.002, False)

            layer = await orchestrate(
                prompt="fix the typo",
                enable_agentic=False,
                dynamic_sys_prompt="sys",
                mode="auto",
            )

        mock_loop_cls.assert_not_called()
        assert layer.generation == "fast path result"

    @pytest.mark.asyncio
    async def test_keyless_local_runtime_uses_cli_directly(self, monkeypatch):
        """A local/Docker runtime with mounted Grok CLI auth should not try
        the xAI API first when no server-side XAI_API_KEY is configured."""
        from src.utils import orchestrate

        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "http")
        monkeypatch.setattr("src.utils.XAI_API_KEY", "")
        monkeypatch.setattr("src.utils.grok_cli_available", lambda: True)

        with patch("src.utils.AgentLoop") as mock_loop_cls, \
             patch("src.utils.resolve_model", new_callable=AsyncMock) as mock_resolve, \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("cli direct result", 0, 0.0, True)

            layer = await orchestrate(
                prompt="fix the typo",
                enable_agentic=True,
                dynamic_sys_prompt="sys",
                mode="auto",
            )

        mock_loop_cls.assert_not_called()
        mock_resolve.assert_not_called()
        assert mock_call.await_args.kwargs["requested_model"] == "grok-composer-2.5-fast"
        assert layer.generation == "cli direct result"
        assert layer.plane == "CLI"
        assert layer.route == "fast"
        assert layer.finish_reason == "final_answer"
        assert layer.model == "grok-composer-2.5-fast"

    @pytest.mark.asyncio
    async def test_keyless_reasoning_runtime_uses_cli_composer(self, monkeypatch):
        """Reasoning requests still work keylessly by selecting the stronger
        CLI model instead of entering API-only thinking/agentic paths."""
        from src.utils import orchestrate

        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "http")
        monkeypatch.setattr("src.utils.XAI_API_KEY", "")
        monkeypatch.setattr("src.utils.grok_cli_available", lambda: True)

        with patch("src.utils.AgentLoop") as mock_loop_cls, \
             patch("src.utils.run_thinking_loop", new_callable=AsyncMock) as mock_thinking, \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("cli reasoning result", 0, 0.0, True)

            layer = await orchestrate(
                prompt="architect the right fix",
                thinking_mode=True,
                enable_agentic=True,
                dynamic_sys_prompt="sys",
                mode="reasoning",
            )

        mock_loop_cls.assert_not_called()
        mock_thinking.assert_not_called()
        assert mock_call.await_args.kwargs["requested_model"] == "grok-composer-2.5-fast"
        assert layer.generation == "cli reasoning result"
        assert layer.plane == "CLI"
        assert layer.route == "fast"
        assert layer.finish_reason == "final_answer"
        assert layer.model == "grok-composer-2.5-fast"

    @pytest.mark.asyncio
    async def test_keyless_cli_failure_does_not_double_fallback(self, monkeypatch):
        """When keyless routing already selected CLI, a CLI failure is the
        final result. It must not call the CLI fallback path a second time."""
        from src.utils import orchestrate

        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "http")
        monkeypatch.setattr("src.utils.XAI_API_KEY", "")
        monkeypatch.setattr("src.utils.grok_cli_available", lambda: True)

        with patch("src.utils.AgentLoop") as mock_loop_cls, \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("Grok CLI error: nope")

            layer = await orchestrate(
                prompt="fix the typo",
                enable_agentic=True,
                dynamic_sys_prompt="sys",
                mode="auto",
            )

        mock_loop_cls.assert_not_called()
        assert mock_call.await_count == 1
        assert layer.generation == "Grok CLI error: nope"
        assert layer.plane == "CLI"
        assert layer.route == "fast"
        assert layer.finish_reason == "error"
        assert layer.model == "grok-composer-2.5-fast"
        assert layer.fallback_occurred is False
        assert layer.routing_receipt["why_detail"] == "cli_failure_api_unavailable"
        assert len(layer.routing_receipt["attempts"]) == 1

    # ── Test 8: mode=reasoning always triggers AgentLoop regardless of prompt ─
    @pytest.mark.asyncio
    async def test_mode_reasoning_always_triggers_agentloop(self):
        """orchestrate(mode='reasoning') must always use AgentLoop,
        even for trivially simple prompts with no keyword matches.
        """
        from src.utils import orchestrate

        simple_prompt = "ok"  # No _REASONING_KEYWORDS match

        resp = _make_response(content="deep answer", tool_calls=[], cost_usd=0.01)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            layer = await orchestrate(
                prompt=simple_prompt,
                mode="reasoning",
                enable_agentic=True,
                dynamic_sys_prompt="sys",
            )

        assert layer.generation == "deep answer"
        # Client must have been constructed (AgentLoop path taken)
        mock_client.chat.create.assert_called_once()

    # ── Test 9: history is correctly injected into AgentLoop ──────────────────
    @pytest.mark.asyncio
    async def test_agentloop_injects_history_correctly(self):
        """AgentLoop.run must prepend the provided conversation history before the prompt."""
        history = [
            {"role": "user", "content": "prior user msg"},
            {"role": "assistant", "content": "prior assistant reply"},
        ]

        resp = _make_response(content="final answer", tool_calls=[], cost_usd=0.001)

        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()

        loop = self._make_loop()

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=self._sync_to_thread):
            layer = await loop.run(prompt="new prompt", history=history)

        assert layer.generation == "final answer"
        
        # Order should be: system prompt, prior user msg, prior assistant reply, new prompt, and the loop response.
        calls = mock_chat.append.call_args_list
        assert len(calls) == 5


class TestAgentLoopCorrectnessFixes:
    """Now#2 fixes: capped tool_calls get tool_results, budget-stop synthesizes
    a final answer, and the caller's policy instance is never mutated."""

    def _make_loop(self, policy: AgentLoopPolicy = None):
        policy = policy or AgentLoopPolicy()
        return AgentLoop(policy=policy, dynamic_sys_prompt="You are helpful.", model="grok-4.3")

    @pytest.mark.asyncio
    async def test_capped_tool_calls_all_receive_tool_results(self):
        """Tool calls dropped by max_tool_calls_per_turn must still receive a
        synthesized tool_result — the full assistant response (ALL tool_calls)
        is appended to the chat, so orphaned ids would poison the next sample.
        """
        async def ok_tool() -> str:
            return "ran"

        register_internal_tool("__cap_tool__", ok_tool)

        def _make_tc(call_id):
            tc = MagicMock()
            tc.id = call_id
            tc.function.name = "__cap_tool__"
            tc.function.arguments = "{}"
            return tc

        resp_tools = _make_response(
            content="",
            tool_calls=[_make_tc("call-1"), _make_tc("call-2"), _make_tc("call-3")],
            cost_usd=0.001,
        )
        resp_final = _make_response(content="done", tool_calls=[], cost_usd=0.001)

        call_count = 0
        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return resp_tools if call_count == 1 else resp_final

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_tool_result = MagicMock(side_effect=lambda result, tool_call_id=None: (result, tool_call_id))

        loop = self._make_loop(AgentLoopPolicy(max_depth=3, max_tool_calls_per_turn=1))

        try:
            with patch("xai_sdk.Client", return_value=mock_client), \
                 patch("xai_sdk.chat.tool_result", new=mock_tool_result):
                layer = await loop.run("use tools")
        finally:
            _INTERNAL_TOOL_REGISTRY.pop("__cap_tool__", None)

        assert layer.generation == "done"
        # Every tool_call id gets a tool_result: the dispatched one first, then
        # the two dropped by the cap.
        injected_ids = [c.kwargs["tool_call_id"] for c in mock_tool_result.call_args_list]
        assert injected_ids == ["call-1", "call-2", "call-3"]
        skipped_texts = [c.args[0] for c in mock_tool_result.call_args_list[1:]]
        assert all(
            "skipped: per-turn tool-call cap (1) reached" in text
            for text in skipped_texts
        )

    @pytest.mark.asyncio
    async def test_budget_stop_after_dispatch_synthesizes_final_answer(self):
        """After a post-dispatch budget stop, the loop must inject the tool
        results and run ONE final sample whose text becomes layer.generation.
        Any tool_calls the final sample requests are ignored, and its cost is
        still counted.
        """
        async def paid_tool() -> str:
            return "image generated\n\n---\n**Cost:** $0.0200"

        register_internal_tool("__paid_synth_tool__", paid_tool)

        tc = MagicMock()
        tc.id = "call-paid"
        tc.function.name = "__paid_synth_tool__"
        tc.function.arguments = "{}"
        resp_tools = _make_response(content="", tool_calls=[tc], cost_usd=0.0)
        resp_synth = _make_response(
            content="synthesized summary", tool_calls=[MagicMock()], cost_usd=0.003
        )

        call_count = 0
        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return resp_tools if call_count == 1 else resp_synth

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = self._make_loop(AgentLoopPolicy(max_depth=5, global_budget_usd=0.01))

        try:
            with patch("xai_sdk.Client", return_value=mock_client), \
                 patch("src.utils.AGENTIC_TOOLS_SCHEMA", []), \
                 patch("src.utils._build_custom_tools", return_value=[]):
                layer = await loop.run("generate an image")
        finally:
            _INTERNAL_TOOL_REGISTRY.pop("__paid_synth_tool__", None)

        assert mock_chat.sample.call_count == 2
        assert layer.generation == "synthesized summary"
        assert layer.finish_reason == "budget_exhausted"
        assert abs(layer.cost_usd - 0.023) < 1e-9

    @pytest.mark.asyncio
    async def test_budget_stop_before_dispatch_labels_budget_exhausted(self):
        """A budget stop right after a sample (before any tool dispatch) must
        label the run budget_exhausted and keep that response's content."""
        resp = _make_response(content="expensive reasoning", tool_calls=[], cost_usd=0.60)

        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = self._make_loop(AgentLoopPolicy(max_depth=5, global_budget_usd=0.50))

        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await loop.run("expensive prompt")

        mock_chat.sample.assert_called_once_with()
        assert layer.generation == "expensive reasoning"
        assert layer.finish_reason == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_agentloop_does_not_mutate_caller_policy(self):
        """AgentLoop must scale observation limits on its own policy copy —
        the caller's shared AgentLoopPolicy instance stays untouched."""
        shared_policy = AgentLoopPolicy()
        loop = self._make_loop(shared_policy)
        assert loop.policy is not shared_policy

        resp = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()

        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_model = MagicMock()
        mock_model.max_prompt_length = 262144
        mock_client.models.get_language_model.return_value = mock_model

        with patch("xai_sdk.Client", return_value=mock_client):
            await loop.run("prompt")

        # run() scaled the copy, not the shared instance
        assert loop.policy.max_obs_chars == 32000
        assert shared_policy.max_obs_chars == 8000
        assert shared_policy.max_obs_tokens == 2000

    def test_get_model_max_tokens_caches_api_lookup(self):
        """Successful API lookups must be cached with a TTL so agent runs stop
        paying a synchronous SDK network call each time."""
        from src.utils import _MODEL_MAX_TOKENS_CACHE, get_model_max_tokens

        _MODEL_MAX_TOKENS_CACHE.clear()
        mock_model = MagicMock()
        mock_model.max_prompt_length = 262144
        mock_client = MagicMock()
        mock_client.models.get_language_model.return_value = mock_model

        with patch("src.utils.get_xai_client", return_value=mock_client):
            assert get_model_max_tokens("grok-4.3") == 262144
            assert get_model_max_tokens("grok-4.3") == 262144

        # Second call served from cache — only one SDK lookup
        assert mock_client.models.get_language_model.call_count == 1

        # Expired entries are refetched
        value, ts = _MODEL_MAX_TOKENS_CACHE["grok-4.3"]
        _MODEL_MAX_TOKENS_CACHE["grok-4.3"] = (value, ts - 10_000)
        with patch("src.utils.get_xai_client", return_value=mock_client):
            assert get_model_max_tokens("grok-4.3") == 262144
        assert mock_client.models.get_language_model.call_count == 2


class TestDefaultAgenticRouting:
    """Now#1: AgentLoop is the default execution path. The keyword heuristic
    only selects the model; UNIGROK_FORCE_FAST is the fast-path kill-switch."""

    def _mock_client(self, resp):
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        mock_client.close = MagicMock()
        return mock_client

    @pytest.mark.asyncio
    async def test_default_prompt_routes_through_agentloop(self, monkeypatch):
        """A plain prompt with zero reasoning keywords must still run the
        AgentLoop (previously it was keyword-gated onto the toolless fast path)
        and select the coding model."""
        from src.utils import DEFAULT_CODING_MODEL, orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        resp = _make_response(content="tool-aware answer", tool_calls=[], cost_usd=0.001)
        mock_client = self._mock_client(resp)

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            layer = await orchestrate(
                prompt="fix the typo on line 3",
                mode="auto",
                dynamic_sys_prompt="sys",
            )

        mock_call.assert_not_awaited()
        mock_client.chat.create.assert_called_once()
        assert mock_client.chat.create.call_args.kwargs["model"] == DEFAULT_CODING_MODEL
        assert layer.route == "agentic"
        assert layer.generation == "tool-aware answer"
        assert layer.finish_reason == "final_answer"

    @pytest.mark.asyncio
    async def test_reasoning_prompt_selects_planning_model(self, monkeypatch):
        """Reasoning-scored prompts pick DEFAULT_PLANNING_MODEL — the heuristic
        selects the model instead of gating agent access."""
        from src.utils import DEFAULT_PLANNING_MODEL, orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        resp = _make_response(content="planned answer", tool_calls=[], cost_usd=0.001)
        mock_client = self._mock_client(resp)

        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await orchestrate(
                prompt="audit the architecture of this gateway and plan a redesign",
                mode="auto",
                dynamic_sys_prompt="sys",
            )

        assert mock_client.chat.create.call_args.kwargs["model"] == DEFAULT_PLANNING_MODEL
        assert layer.generation == "planned answer"

    @pytest.mark.asyncio
    async def test_unigrok_force_fast_bypasses_agentloop(self, monkeypatch):
        """The UNIGROK_FORCE_FAST env kill-switch must route to the fast path
        without ever constructing an AgentLoop."""
        from src.utils import orchestrate

        monkeypatch.setenv("UNIGROK_FORCE_FAST", "1")
        with patch("src.utils.AgentLoop") as mock_loop_cls, \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("fast answer", 10, 0.001, False)
            layer = await orchestrate(prompt="hello", mode="auto", dynamic_sys_prompt="sys")

        mock_loop_cls.assert_not_called()
        assert layer.generation == "fast answer"
        assert layer.route == "fast"
        assert layer.finish_reason == "final_answer"

    @pytest.mark.asyncio
    async def test_native_cli_does_not_inherit_gateway_react_depth(self, monkeypatch):
        """Native Grok owns its agent loop; the gateway's eight-step ReAct
        guard must not become an implicit CLI --max-turns failure."""
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        selection = (
            "grok-composer-2.5-fast",
            "cost",
            {
                "resolved_model": "grok-composer-2.5-fast",
                "catalog": {"source": "grok_cli_live", "fallback": False},
            },
            False,
        )
        with patch(
            "src.utils._select_routing_model", new=AsyncMock(return_value=selection)
        ), patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("complete answer", 0, 0.0, True)
            layer = await orchestrate(
                prompt="finish the implementation",
                mode="auto",
                dynamic_sys_prompt="sys",
            )

        assert mock_call.await_args.kwargs["max_turns"] is None
        assert layer.generation == "complete answer"
        assert layer.finish_reason == "final_answer"

    @pytest.mark.asyncio
    async def test_agentloop_failure_falls_back_with_fallback_label(self, monkeypatch):
        """When AgentLoop raises, the fast-path result must be labeled
        finish_reason='fallback' — not a clean final_answer."""
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(side_effect=RuntimeError("loop exploded"))

        with patch("src.utils.AgentLoop", return_value=mock_loop), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("recovered answer", 10, 0.001, False)
            layer = await orchestrate(prompt="hello", mode="auto", dynamic_sys_prompt="sys")

        assert layer.generation == "recovered answer"
        assert layer.finish_reason == "fallback"


class TestSymmetricXaiPlaneFailover:
    """CLI-first Grok work recovers only on the same provider's API plane."""

    @staticmethod
    def _cli_selection():
        return (
            "grok-composer-2.5-fast",
            "cost",
            {
                "route_class": "coding",
                "resolved_model": "grok-composer-2.5-fast",
                "catalog": {"source": "grok_cli_live", "fallback": False},
            },
            False,
        )

    @staticmethod
    def _api_selection():
        return (
            "grok-build-0.1",
            "auto",
            {
                "route_class": "coding",
                "resolved_model": "grok-build-0.1",
                "catalog": {"source": "xai_api_live", "fallback": False},
            },
            False,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("requested_plane", ("auto", "cli"))
    async def test_cli_failure_recovers_on_xai_api_with_receipt(
        self, requested_plane
    ):
        from src.utils import orchestrate

        secret = "xai-123456789CLI"
        with patch("src.utils.xai_api_key_configured", return_value=True), \
             patch(
                 "src.utils._select_routing_model",
                 new=AsyncMock(
                     side_effect=[self._cli_selection(), self._api_selection()]
                 ),
            ) as mock_select, \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                RuntimeError(f"CLI transport unavailable: Bearer {secret}"),
                ("API recovered answer", 42, 0.012, False),
            ]
            layer = await orchestrate(
                prompt="complete the task",
                session="grok-owned-session",
                mode="auto",
                enable_agentic=False,
                dynamic_sys_prompt="Objective TTL: 2030-01-01T00:00:00Z",
                requested_plane=requested_plane,
                fallback_policy="cross_plane",
            )

        assert mock_select.await_count == 2
        assert mock_select.await_args_list[1].kwargs["requested_plane"] == "api"
        assert mock_call.await_count == 2
        assert mock_call.await_args_list[0].args[0] == "cli-fallback"
        assert mock_call.await_args_list[1].args[0] == "reasoning"
        assert mock_call.await_args_list[0].args[1:4] == (
            "complete the task",
            "grok-owned-session",
            None,
        )
        assert mock_call.await_args_list[1].args[1:4] == (
            "complete the task",
            "grok-owned-session",
            None,
        )
        assert "Objective TTL: 2030-01-01T00:00:00Z" in (
            mock_call.await_args_list[1].args[4]
        )
        assert "Re-observe the current state" in mock_call.await_args_list[1].args[4]
        assert secret not in mock_call.await_args_list[1].args[4]
        assert mock_call.await_args_list[1].kwargs["requested_model"] == "grok-build-0.1"
        assert layer.generation == "API recovered answer"
        assert layer.plane == "API"
        assert layer.model == "grok-build-0.1"
        assert layer.finish_reason == "fallback"
        assert layer.degraded is True
        assert layer.fallback_occurred is True
        assert layer.routing_receipt["provider"] == "xai"
        assert layer.routing_receipt["authority"] == "grok"
        assert layer.routing_receipt["why_detail"] == "cli_to_api_fallback"
        assert layer.routing_receipt["resolved_plane"] == "API"
        assert layer.routing_receipt["billing_class"] == "metered"
        attempts = layer.routing_receipt["attempts"]
        assert [attempt["attempt"] for attempt in attempts] == [1, 2]
        assert [attempt["plane"] for attempt in attempts] == ["CLI", "API"]
        assert [attempt["model"] for attempt in attempts] == [
            "grok-composer-2.5-fast",
            "grok-build-0.1",
        ]
        assert [attempt["outcome"] for attempt in attempts] == [
            "error",
            "completed",
        ]
        assert attempts[0]["error_type"] == "RuntimeError"
        assert attempts[0]["error_digest"]
        assert attempts[0]["billing_source"] == "subscription_unmetered"
        assert attempts[0]["cost_usd"] == 0.0
        assert attempts[1]["billing_source"] == "xai_response_exact"
        assert attempts[1]["cost_usd"] == pytest.approx(0.012)
        assert attempts[1]["tokens"] == 42
        assert secret not in json.dumps(layer.routing_receipt)
        assert layer.routing_receipt["failure"]["error"] == attempts[0]["error"]

    @pytest.mark.asyncio
    async def test_cli_failure_preserves_agentic_semantics_on_api(self):
        from src.utils import MetaLayer, orchestrate

        api_layer = MetaLayer(
            generation="agentic API recovery",
            finish_reason="final_answer",
            tokens=25,
            cost_usd=0.01,
        )
        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value=api_layer)

        with patch("src.utils.xai_api_key_configured", return_value=True), \
             patch(
                 "src.utils._select_routing_model",
                 new=AsyncMock(
                     side_effect=[self._cli_selection(), self._api_selection()]
                 ),
             ), \
             patch(
                 "src.utils._call_plane",
                 new=AsyncMock(side_effect=RuntimeError("CLI failed")),
             ) as mock_call, \
             patch("src.utils.AgentLoop", return_value=mock_loop) as loop_cls:
            layer = await orchestrate(
                prompt="complete the repository task",
                mode="auto",
                enable_agentic=True,
                requested_plane="auto",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 1
        assert loop_cls.call_args.kwargs["model"] == "grok-build-0.1"
        assert "Re-observe the current state" in loop_cls.call_args.kwargs[
            "dynamic_sys_prompt"
        ]
        mock_loop.run.assert_awaited_once()
        assert layer.generation == "agentic API recovery"
        assert layer.route == "agentic"
        assert layer.plane == "API"
        assert layer.finish_reason == "fallback"
        assert layer.routing_receipt["why_detail"] == "cli_to_api_fallback"

    @pytest.mark.asyncio
    async def test_cli_to_api_agentic_recovery_uses_injected_store_history(self):
        from src.utils import MetaLayer, orchestrate

        injected_store = MagicMock(name="injected_store")
        injected_store.get_similar_task_memories = AsyncMock(return_value=[])
        injected_store.save_telemetry = AsyncMock()
        injected_store.save_task_memory = AsyncMock()
        expected_history = [
            {"role": "user", "content": "prior objective"},
            {"role": "assistant", "content": "prior verified receipt"},
        ]
        history_loader = AsyncMock(return_value=expected_history)
        api_layer = MetaLayer(
            generation="agentic API recovery",
            finish_reason="final_answer",
            tokens=25,
            cost_usd=0.01,
        )
        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value=api_layer)

        with patch("src.utils.xai_api_key_configured", return_value=True), patch(
            "src.utils._select_routing_model",
            new=AsyncMock(
                side_effect=[self._cli_selection(), self._api_selection()]
            ),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(side_effect=RuntimeError("CLI failed")),
        ), patch("src.utils.AgentLoop", return_value=mock_loop), patch(
            "src.utils.load_history", new=history_loader
        ):
            await orchestrate(
                prompt="continue the objective",
                session="grok-owned-session",
                store=injected_store,
                enable_agentic=True,
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        history_loader.assert_awaited_once_with(
            "grok-owned-session", injected_store
        )
        assert mock_loop.run.await_args.kwargs["history"] == expected_history

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "requested_plane",
        (
            "cli",
            "auto",
        ),
    )
    async def test_same_plane_contract_refuses_api_fallback(self, requested_plane):
        from src.utils import orchestrate

        mock_select = AsyncMock(return_value=self._cli_selection())
        with patch("src.utils.xai_api_key_configured", return_value=True), \
             patch("src.utils._select_routing_model", new=mock_select), \
             patch(
                 "src.utils._call_plane",
                 new=AsyncMock(side_effect=RuntimeError("CLI failed")),
             ) as mock_call:
            layer = await orchestrate(
                prompt="stay on subscription",
                mode="auto",
                enable_agentic=False,
                requested_plane=requested_plane,
                fallback_policy="same_plane",
            )

        assert mock_select.await_count == 1
        assert mock_call.await_count == 1
        assert layer.plane == "CLI"
        assert layer.finish_reason == "error"
        assert layer.degraded is False
        assert layer.fallback_occurred is False
        assert layer.routing_receipt["why_detail"] == "same_plane_failure"
        assert layer.routing_receipt["fallback_occurred"] is False
        assert len(layer.routing_receipt["attempts"]) == 1

    @pytest.mark.asyncio
    async def test_api_same_plane_terminal_persists_exact_attempt_receipt(self):
        from src.utils import orchestrate

        store = MagicMock()
        store.get_similar_task_memories = AsyncMock(return_value=[])
        store.save_telemetry = AsyncMock()
        store.save_task_memory = AsyncMock()
        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._api_selection()),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(side_effect=RuntimeError("API unavailable")),
        ):
            layer = await orchestrate(
                prompt="stay on the xAI API plane",
                store=store,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="same_plane",
            )

        store.save_telemetry.assert_awaited_once()
        saved = store.save_telemetry.await_args
        assert saved.kwargs["routing"] == layer.routing_receipt
        assert saved.kwargs["routing"]["authority"] == "grok"
        assert saved.kwargs["routing"]["attempts"][0]["outcome"] == "error"
        assert saved.kwargs["billing_source"] == "unknown"
        assert saved.kwargs["token_kind"] == "unavailable"

    @pytest.mark.asyncio
    async def test_api_only_terminal_persists_exact_attempt_receipt(self):
        from src.utils import orchestrate

        store = MagicMock()
        store.get_similar_task_memories = AsyncMock(return_value=[])
        store.save_telemetry = AsyncMock()
        store.save_task_memory = AsyncMock()
        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._api_selection()),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(side_effect=RuntimeError("research API unavailable")),
        ):
            layer = await orchestrate(
                prompt="Research current evidence",
                mode="research",
                store=store,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert layer.routing_receipt["why_detail"] == "cross_plane_incompatible"
        store.save_telemetry.assert_awaited_once()
        saved = store.save_telemetry.await_args
        assert saved.kwargs["routing"] == layer.routing_receipt
        assert saved.kwargs["routing"]["attempts"][0]["plane"] == "API"
        assert saved.kwargs["billing_source"] == "unknown"

    @pytest.mark.asyncio
    async def test_direct_cli_success_records_one_physical_attempt(self):
        from src.utils import orchestrate

        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._cli_selection()),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(return_value=("CLI answer", 0, 0.0, True)),
        ):
            layer = await orchestrate(
                prompt="complete the task",
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="same_plane",
            )

        assert layer.finish_reason == "final_answer"
        assert layer.routing_receipt["authority"] == "grok"
        assert layer.routing_receipt["attempts"] == [
            {
                "provider": "xai",
                "phase": "execution",
                "attempt": 1,
                "plane": "CLI",
                "model": "grok-composer-2.5-fast",
                "purpose": "fast",
                "outcome": "completed",
                "billing_class": "subscription",
                "billing_source": "subscription_unmetered",
                "usage_source": "subscription_unmetered",
                "cost_usd": 0.0,
            }
        ]

    @pytest.mark.asyncio
    async def test_cli_and_api_failure_is_terminal_without_fallback_loop(self):
        from src.utils import orchestrate

        with patch("src.utils.xai_api_key_configured", return_value=True), \
             patch(
                 "src.utils._select_routing_model",
                 new=AsyncMock(
                     side_effect=[self._cli_selection(), self._api_selection()]
                 ),
             ), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                RuntimeError("CLI failed"),
                RuntimeError("API failed"),
            ]
            layer = await orchestrate(
                prompt="complete the task",
                mode="auto",
                enable_agentic=False,
                requested_plane="auto",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 2
        assert layer.finish_reason == "error"
        assert layer.plane == "API"
        assert layer.degraded is True
        assert layer.fallback_occurred is True
        assert layer.routing_receipt["why_detail"] == "cli_and_api_failed"
        assert [attempt["plane"] for attempt in layer.routing_receipt["attempts"]] == [
            "CLI",
            "API",
        ]
        assert all(
            attempt["outcome"] == "error"
            for attempt in layer.routing_receipt["attempts"]
        )

    @pytest.mark.asyncio
    async def test_explicit_api_start_can_recover_on_cli_when_policy_crosses(self):
        from src.utils import orchestrate

        mock_select = AsyncMock(return_value=self._api_selection())
        secret = "xai-123456789SECRET"
        messages = [
            {"role": "user", "content": "Earlier objective"},
            {"role": "assistant", "content": "Earlier observation"},
            {"role": "user", "content": "complete the task"},
        ]
        with patch("src.utils._select_routing_model", new=mock_select), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                RuntimeError(f"API failed with Bearer {secret}"),
                ("CLI recovered answer", 0, 0.0, True),
            ]
            layer = await orchestrate(
                prompt="complete the task",
                session="grok-session",
                mode="auto",
                enable_agentic=False,
                dynamic_sys_prompt="Objective TTL: 2030-01-01T00:00:00Z",
                input_messages=messages,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert mock_select.await_count == 2
        assert mock_select.await_args_list[1].kwargs["requested_plane"] == "cli"
        assert mock_call.await_count == 2
        assert mock_call.await_args_list[0].args[0] == "reasoning"
        assert mock_call.await_args_list[1].args[0] == "cli-fallback"
        assert mock_call.await_args_list[1].args[2] == "grok-session"
        assert mock_call.await_args_list[1].kwargs["input_messages"] == messages
        fallback_system = mock_call.await_args_list[1].args[4]
        assert "Objective TTL: 2030-01-01T00:00:00Z" in fallback_system
        assert "Re-observe current state" in fallback_system
        assert secret not in fallback_system
        assert layer.generation == "CLI recovered answer"
        assert layer.plane == "CLI-Fallback"
        assert layer.finish_reason == "fallback"
        assert layer.routing_receipt["why_detail"] == "api_to_cli_fallback"
        assert [item["plane"] for item in layer.routing_receipt["attempts"]] == [
            "API",
            "CLI",
        ]
        assert layer.routing_receipt["attempts"][0]["billing_source"] == (
            "unknown_after_failure"
        )
        assert layer.routing_receipt["attempts"][0]["cost_usd"] is None
        assert layer.routing_receipt["attempts"][1]["billing_source"] == (
            "subscription_unmetered"
        )
        receipt_text = json.dumps(layer.routing_receipt)
        assert secret not in receipt_text
        assert "[REDACTED" in receipt_text

    @pytest.mark.asyncio
    async def test_cli_cross_actual_selection_moves_api_only_request_to_api(self):
        from src.utils import orchestrate

        research_model = "grok-4.20-multi-agent"
        cli_status = {
            "ready": True,
            "models": ["grok-composer-2.5-fast"],
            "default_model": "grok-composer-2.5-fast",
        }
        with patch("src.utils.grok_cli_plane_status", return_value=cli_status), \
             patch(
                 "src.utils._MODEL_RESOLVER.catalog_snapshot",
                 new=AsyncMock(
                     return_value=([research_model], "xai_api_live", True)
                 ),
             ), \
             patch(
                 "src.utils._call_plane",
                 new=AsyncMock(return_value=("research answer", 20, 0.01, False)),
             ) as mock_call:
            layer = await orchestrate(
                prompt="Research current evidence",
                mode="research",
                requested_model=research_model,
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 1
        assert mock_call.await_args.args[0] == "reasoning"
        assert layer.generation == "research answer"
        assert layer.plane == "API"
        assert layer.finish_reason == "fallback"
        assert layer.fallback_occurred is True
        assert layer.fallback_occurred == layer.routing_receipt["fallback_occurred"]
        assert layer.routing_receipt["authority"] == "grok"
        assert layer.routing_receipt["why_detail"] == "selection_plane_fallback"
        assert [
            (attempt["plane"], attempt["outcome"])
            for attempt in layer.routing_receipt["selection_attempts"]
        ] == [("CLI", "error"), ("API", "selected")]

    @pytest.mark.asyncio
    async def test_api_plane_is_authoritative_for_slug_also_known_to_cli(self):
        from src.utils import orchestrate

        cli_model = "grok-composer-2.5-fast"
        with patch(
            "src.utils._MODEL_RESOLVER.catalog_snapshot",
            new=AsyncMock(return_value=([cli_model], "xai_api_live", True)),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(return_value=("API answer", 4, 0.01, False)),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                requested_model=cli_model,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="same_plane",
            )

        mock_call.assert_awaited_once()
        assert mock_call.await_args.args[0] == "reasoning"
        assert mock_call.await_args.kwargs["requested_model"] == cli_model
        assert layer.finish_reason == "final_answer"
        assert layer.routing_receipt["requested_plane"] == "API"
        assert layer.routing_receipt["resolved_plane"] == "API"
        assert layer.routing_receipt["fallback_occurred"] is False
        assert layer.plane == "API"

    @pytest.mark.asyncio
    async def test_exact_shared_pin_survives_api_to_cli_execution_fallback(self):
        from src.utils import orchestrate

        cli_model = "grok-composer-2.5-fast"
        cli_status = {
            "ready": True,
            "models": [cli_model],
            "default_model": cli_model,
        }
        with patch(
            "src.utils._MODEL_RESOLVER.catalog_snapshot",
            new=AsyncMock(return_value=([cli_model], "xai_api_live", True)),
        ), patch("src.utils.grok_cli_plane_status", return_value=cli_status), patch(
            "src.utils._call_plane",
            new=AsyncMock(
                side_effect=[
                    RuntimeError("API failed"),
                    ("CLI answer", 0, 0.0, True),
                ]
            ),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                requested_model=cli_model,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 2
        assert [
            item.kwargs["requested_model"] for item in mock_call.await_args_list
        ] == [cli_model, cli_model]
        assert layer.finish_reason == "fallback"
        assert layer.routing_receipt["requested_plane"] == "API"
        assert layer.routing_receipt["resolved_plane"] == "CLI"
        assert layer.routing_receipt["fallback_occurred"] is True
        assert layer.routing_receipt["fallback"]["from_model"] == cli_model
        assert layer.routing_receipt["fallback"]["to_model"] == cli_model

    @pytest.mark.asyncio
    async def test_exact_shared_pin_survives_cli_to_api_execution_fallback(self):
        from src.utils import orchestrate

        shared_model = "grok-shared-exact"
        cli_status = {
            "ready": True,
            "models": [shared_model],
            "default_model": shared_model,
        }
        with patch("src.utils.xai_api_key_configured", return_value=True), patch(
            "src.utils.grok_cli_plane_status", return_value=cli_status
        ), patch(
            "src.utils._MODEL_RESOLVER.catalog_snapshot",
            new=AsyncMock(return_value=([shared_model], "xai_api_live", True)),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(
                side_effect=[
                    RuntimeError("CLI failed"),
                    ("API answer", 8, 0.02, False),
                ]
            ),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                requested_model=shared_model,
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 2
        assert [
            item.kwargs["requested_model"] for item in mock_call.await_args_list
        ] == [shared_model, shared_model]
        assert layer.finish_reason == "fallback"
        assert layer.model == shared_model
        assert layer.routing_receipt["fallback"]["from_model"] == shared_model
        assert layer.routing_receipt["fallback"]["to_model"] == shared_model

    @pytest.mark.asyncio
    async def test_exact_pin_absent_from_alternate_catalog_never_substitutes(self):
        from src.utils import orchestrate

        pinned = "grok-api-only-exact"
        cli_status = {
            "ready": True,
            "models": ["grok-composer-2.5-fast"],
            "default_model": "grok-composer-2.5-fast",
        }
        with patch(
            "src.utils._MODEL_RESOLVER.catalog_snapshot",
            new=AsyncMock(return_value=([pinned], "xai_api_live", True)),
        ), patch(
            "src.utils.grok_cli_plane_status", return_value=cli_status
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(side_effect=RuntimeError("API failed")),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                requested_model=pinned,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        mock_call.assert_awaited_once()
        assert mock_call.await_args.kwargs["requested_model"] == pinned
        assert layer.finish_reason == "error"
        assert layer.routing_receipt["why_detail"] == "api_and_cli_failed"
        assert "will not substitute" in layer.generation
        assert [item["model"] for item in layer.routing_receipt["attempts"]] == [
            pinned
        ]

    @pytest.mark.asyncio
    async def test_agentic_nonanswer_crosses_once_without_same_plane_fast_call(self):
        from src.utils import MetaLayer, orchestrate

        agentic_calls = []

        async def rejected_agentic(
            loop, prompt, session=None, history=None, input_messages=None
        ):
            agentic_calls.append(prompt)
            loop.attempt_recorder(
                plane="API",
                model=loop.model,
                outcome="nonanswer",
                purpose="agentic:depth 1/8",
                tokens=4,
                cost_usd=0.01,
                usage_source="provider_exact",
            )
            return MetaLayer(
                generation="I'll inspect that next.",
                finish_reason="error",
                tokens=4,
                cost_usd=0.01,
                plane="API",
            )

        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(
                side_effect=[self._api_selection(), self._cli_selection()]
            ),
        ), patch("src.utils.AgentLoop.run", new=rejected_agentic), patch(
            "src.utils._call_plane",
            new=AsyncMock(return_value=("CLI recovered", 0, 0.0, True)),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert agentic_calls == ["complete the task"]
        mock_call.assert_awaited_once()
        assert mock_call.await_args.args[0] == "cli-fallback"
        assert layer.finish_reason == "fallback"
        assert [item["plane"] for item in layer.routing_receipt["attempts"]] == [
            "API",
            "CLI",
        ]

    @pytest.mark.asyncio
    async def test_thinking_nonanswer_crosses_once_without_api_fast_call(self):
        from src.utils import MetaLayer, orchestrate

        async def rejected_thinking(*_args, attempt_recorder=None, **_kwargs):
            attempt_recorder(
                plane="API",
                model="grok-build-0.1",
                outcome="nonanswer",
                purpose="thinking:depth 1/8",
                tokens=5,
                cost_usd=0.02,
                usage_source="provider_exact",
            )
            return MetaLayer(
                generation="Let me work on that.",
                finish_reason="error",
                tokens=5,
                cost_usd=0.02,
                plane="API",
            )

        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(
                side_effect=[self._api_selection(), self._cli_selection()]
            ),
        ), patch(
            "src.utils.run_thinking_loop", new=rejected_thinking
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(return_value=("CLI recovered", 0, 0.0, True)),
        ) as mock_call:
            layer = await orchestrate(
                prompt="solve the hard task",
                thinking_mode=True,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        mock_call.assert_awaited_once()
        assert mock_call.await_args.args[0] == "cli-fallback"
        assert layer.finish_reason == "fallback"
        assert [item["plane"] for item in layer.routing_receipt["attempts"]] == [
            "API",
            "CLI",
        ]

    @pytest.mark.asyncio
    async def test_api_only_runtime_failure_never_falls_back_to_cli(self):
        from src.utils import orchestrate

        research_model = "grok-4.20-multi-agent"
        with patch(
            "src.utils._MODEL_RESOLVER.catalog_snapshot",
            new=AsyncMock(return_value=([research_model], "xai_api_live", True)),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(side_effect=RuntimeError("API research failed")),
        ) as mock_call:
            layer = await orchestrate(
                prompt="Research current evidence",
                mode="research",
                requested_model=research_model,
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 1
        assert layer.finish_reason == "error"
        assert layer.plane == "API"
        assert layer.routing_receipt["why_detail"] == "cross_plane_incompatible"
        assert [attempt["plane"] for attempt in layer.routing_receipt["attempts"]] == [
            "API"
        ]

    @pytest.mark.asyncio
    async def test_api_only_selection_fallback_failure_does_not_loop_to_cli(self):
        from src.utils import orchestrate

        research_model = "grok-4.20-multi-agent"
        cli_status = {
            "ready": True,
            "models": ["grok-composer-2.5-fast"],
            "default_model": "grok-composer-2.5-fast",
        }
        with patch("src.utils.grok_cli_plane_status", return_value=cli_status), \
             patch(
                 "src.utils._MODEL_RESOLVER.catalog_snapshot",
                 new=AsyncMock(
                     return_value=([research_model], "xai_api_live", True)
                 ),
             ), \
             patch(
                 "src.utils._call_plane",
                 new=AsyncMock(side_effect=RuntimeError("API research failed")),
             ) as mock_call:
            layer = await orchestrate(
                prompt="Research current evidence",
                mode="research",
                requested_model=research_model,
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 1
        assert layer.finish_reason == "error"
        assert layer.plane == "API"
        assert layer.fallback_occurred is True
        assert layer.degraded is True
        assert layer.routing_receipt["fallback_occurred"] is True
        assert layer.routing_receipt["why_detail"] == (
            "selection_fallback_execution_failed"
        )
        assert [item["plane"] for item in layer.routing_receipt["attempts"]] == [
            "API"
        ]

    @pytest.mark.asyncio
    async def test_profile_failure_can_cross_once_during_selection(self):
        from src.utils import orchestrate

        def _profile(model):
            if model == "grok-composer-2.5-fast":
                raise RuntimeError("CLI profile unavailable")
            return {"profile": "api-profile", "reasoning_effort": "high"}

        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(
                side_effect=[self._cli_selection(), self._api_selection()]
            ),
        ), patch("src.utils.load_grok_profile", side_effect=_profile), patch(
            "src.utils._call_plane",
            new=AsyncMock(return_value=("API answer", 12, 0.003, False)),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 1
        assert layer.generation == "API answer"
        assert layer.finish_reason == "fallback"
        selection_attempts = layer.routing_receipt["selection_attempts"]
        assert [item["outcome"] for item in selection_attempts] == [
            "error",
            "selected",
        ]
        assert selection_attempts[0]["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_reasoning_requirement_failure_can_cross_once_during_selection(self):
        from src.utils import orchestrate

        def _profile(model):
            effort = "low" if model == "grok-composer-2.5-fast" else "high"
            return {"profile": f"{effort}-profile", "reasoning_effort": effort}

        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(
                side_effect=[self._cli_selection(), self._api_selection()]
            ),
        ), patch("src.utils.load_grok_profile", side_effect=_profile), patch(
            "src.utils._call_plane",
            new=AsyncMock(return_value=("API answer", 12, 0.003, False)),
        ) as mock_call:
            layer = await orchestrate(
                prompt="complete the task",
                enable_agentic=False,
                require_reasoning_level="high",
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 1
        assert layer.finish_reason == "fallback"
        assert [
            (item["plane"], item["outcome"])
            for item in layer.routing_receipt["selection_attempts"]
        ] == [("CLI", "error"), ("API", "selected")]
        assert "reasoning effort" in layer.routing_receipt["selection_attempts"][0][
            "error"
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("policy", "side_effects", "expected_detail"),
        (
            ("same_plane", [RuntimeError("selection unavailable")], "selection_failed"),
            (
                "cross_plane",
                [RuntimeError("CLI unavailable"), RuntimeError("API unavailable")],
                "selection_failed_both_planes",
            ),
        ),
    )
    async def test_selection_failures_are_persisted_with_exact_receipt(
        self, tmp_path, policy, side_effects, expected_detail
    ):
        from src.utils import GrokSessionStore, orchestrate

        store = GrokSessionStore(db_path=tmp_path / f"{expected_detail}.db")
        try:
            with patch(
                "src.utils._select_routing_model",
                new=AsyncMock(side_effect=side_effects),
            ):
                layer = await orchestrate(
                    prompt="preserve the failed selection",
                    store=store,
                    enable_agentic=False,
                    requested_plane="cli",
                    fallback_policy=policy,
                )

            assert layer.routing_receipt["why_detail"] == expected_detail
            rows = await store.get_telemetry_stats()
            assert len(rows) == 1
            assert json.loads(rows[0]["metadata"])["routing"] == (
                layer.routing_receipt
            )
            memories = await store.get_recent_model_stats()
            assert memories == [
                {
                    "plane": "local",
                    "model": "unresolved",
                    "samples": 1,
                    "success_rate": 0.0,
                    "avg_cost": 0.0,
                    "avg_latency": pytest.approx(layer.latency),
                }
            ]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_api_and_cli_failure_redacts_both_plane_errors(self, caplog):
        from src.utils import orchestrate

        api_secret = "xai-123456789API"
        cli_secret = "xai-123456789CLI"
        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._api_selection()),
        ), patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                RuntimeError(f"API failed: Bearer {api_secret}"),
                RuntimeError(f"CLI failed: Bearer {cli_secret}"),
            ]
            layer = await orchestrate(
                prompt="complete the task",
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 2
        assert layer.finish_reason == "error"
        rendered = json.dumps(
            {"generation": layer.generation, "receipt": layer.routing_receipt}
        )
        assert api_secret not in rendered
        assert cli_secret not in rendered
        assert rendered.count("[REDACTED") >= 2
        assert api_secret not in caplog.text
        assert cli_secret not in caplog.text

    @pytest.mark.asyncio
    async def test_cloudrun_api_failure_returns_redacted_authority_receipt(self):
        from src.utils import orchestrate

        secret = "xai-123456789CLOUD"
        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._api_selection()),
        ), patch(
            "src.utils._call_plane",
            new=AsyncMock(side_effect=RuntimeError(f"Bearer {secret}")),
        ), patch("src.utils.is_cloudrun_runtime", return_value=True):
            layer = await orchestrate(
                prompt="complete the task",
                enable_agentic=False,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert layer.finish_reason == "error"
        assert layer.plane == "API"
        assert layer.routing_receipt["provider"] == "xai"
        assert layer.routing_receipt["authority"] == "grok"
        assert layer.routing_receipt["why_detail"] == "cli_unavailable_in_cloudrun"
        assert len(layer.routing_receipt["attempts"]) == 1
        rendered = json.dumps(
            {"generation": layer.generation, "receipt": layer.routing_receipt}
        )
        assert secret not in rendered

    @pytest.mark.asyncio
    async def test_repeated_cli_nonanswer_recovers_on_xai_api(self):
        from src.utils import orchestrate

        promise = "I'll run the audit now and report back."
        with patch("src.utils.xai_api_key_configured", return_value=True), \
             patch(
                 "src.utils._select_routing_model",
                 new=AsyncMock(
                     side_effect=[self._cli_selection(), self._api_selection()]
                 ),
             ), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                (promise, 0, 0.0, True),
                (promise, 0, 0.0, True),
                ("API delivered the result.", 18, 0.004, False),
            ]
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                enable_agentic=False,
                requested_plane="auto",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 3
        assert [call.args[0] for call in mock_call.await_args_list] == [
            "cli-fallback",
            "cli-fallback",
            "reasoning",
        ]
        assert layer.generation == "API delivered the result."
        assert layer.finish_reason == "fallback"
        assert layer.plane == "API"
        assert layer.routing_receipt["why_detail"] == "cli_to_api_fallback"
        assert layer.routing_receipt["completion_recovery"] == {
            "attempted": True,
            "reason": "nonanswer_completion",
            "succeeded": False,
            "attempts": 1,
        }
        assert [
            (item["attempt"], item["plane"], item["outcome"])
            for item in layer.routing_receipt["attempts"]
        ] == [
            (1, "CLI", "nonanswer"),
            (2, "CLI", "nonanswer"),
            (3, "API", "completed"),
        ]

    @pytest.mark.asyncio
    async def test_repeated_cli_nonanswer_stays_cli_with_same_plane_policy(self):
        from src.utils import orchestrate

        promise = "I'll run the audit now and report back."
        mock_select = AsyncMock(return_value=self._cli_selection())
        with patch("src.utils.xai_api_key_configured", return_value=True), \
             patch("src.utils._select_routing_model", new=mock_select), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                (promise, 0, 0.0, True),
                (promise, 0, 0.0, True),
            ]
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="same_plane",
            )

        assert mock_select.await_count == 1
        assert mock_call.await_count == 2
        assert layer.finish_reason == "error"
        assert layer.plane == "CLI"
        assert layer.fallback_occurred is False
        assert layer.routing_receipt["why_detail"] == "same_plane_failure"
        assert layer.routing_receipt["completion_recovery"]["attempted"] is True

    @pytest.mark.asyncio
    async def test_repeated_api_nonanswer_can_recover_on_cli(self):
        from src.utils import orchestrate

        promise = "I'll run the audit now and report back."
        store = MagicMock()
        store.get_similar_task_memories = AsyncMock(return_value=[])
        store.save_telemetry = AsyncMock()
        store.save_task_memory = AsyncMock()
        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._api_selection()),
        ), patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                (promise, 7, 0.001, False),
                (promise, 8, 0.002, False),
                ("CLI delivered the result.", 0, 0.0, True),
            ]
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                enable_agentic=False,
                store=store,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 3
        assert layer.generation == "CLI delivered the result."
        assert layer.finish_reason == "fallback"
        assert layer.routing_receipt["why_detail"] == "api_to_cli_fallback"
        assert [
            (item["plane"], item["outcome"], item["cost_usd"])
            for item in layer.routing_receipt["attempts"]
        ] == [
            ("API", "nonanswer", 0.001),
            ("API", "nonanswer", 0.002),
            ("CLI", "completed", 0.0),
        ]
        assert layer.cost_usd == pytest.approx(0.003)
        assert layer.tokens == 15
        assert store.save_telemetry.await_args.args[4] == pytest.approx(0.003)
        assert store.save_telemetry.await_args.kwargs["tokens"] == 15
        assert store.save_telemetry.await_args.kwargs["token_kind"] == "partial"
        assert store.save_telemetry.await_args.kwargs["billing_source"] == (
            "xai_response_exact"
        )

    @pytest.mark.asyncio
    async def test_known_api_usage_survives_api_retry_and_cli_failure(self):
        from src.utils import orchestrate

        promise = "I'll run the audit now and report back."
        store = MagicMock()
        store.get_similar_task_memories = AsyncMock(return_value=[])
        store.save_telemetry = AsyncMock()
        store.save_task_memory = AsyncMock()
        with patch(
            "src.utils._select_routing_model",
            new=AsyncMock(return_value=self._api_selection()),
        ), patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                (promise, 7, 0.001, False),
                RuntimeError("API correction failed"),
                RuntimeError("CLI recovery failed"),
            ]
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                enable_agentic=False,
                store=store,
                requested_plane="api",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 3
        assert layer.finish_reason == "error"
        assert layer.cost_usd == pytest.approx(0.001)
        assert layer.tokens == 7
        assert store.save_telemetry.await_args.args[4] == pytest.approx(0.001)
        assert store.save_telemetry.await_args.kwargs["tokens"] == 7
        assert store.save_telemetry.await_args.kwargs["token_kind"] == "partial"
        assert store.save_telemetry.await_args.kwargs["billing_source"] == "partial"

    def test_partial_provider_usage_is_never_labeled_exact(self):
        from src.utils import (
            _provider_response_usage,
            _telemetry_usage_kwargs,
            _xai_execution_attempt_receipt,
        )

        response = MagicMock()
        response.usage.prompt_tokens = 7
        response.usage.completion_tokens = None
        response.cost_usd = 0.004
        tokens, cost, source = _provider_response_usage(response)
        attempt = _xai_execution_attempt_receipt(
            1,
            plane="API",
            model="grok-shared",
            outcome="completed",
            purpose="fast",
            tokens=tokens,
            cost_usd=cost,
            usage_source=source,
        )
        telemetry = _telemetry_usage_kwargs(
            plane="API",
            model="grok-shared",
            tokens=tokens or 0,
            prompt="task",
            output="answer",
            routing={"attempts": [attempt]},
        )

        assert (tokens, cost, source) == (7, 0.004, "partial")
        assert attempt["billing_source"] == "partial"
        assert telemetry["token_kind"] == "partial"
        assert telemetry["billing_source"] == "partial"

    @pytest.mark.parametrize(
        "attempt_planes",
        (("CLI", "API"), ("API", "CLI")),
        ids=("cli-to-api", "api-to-cli"),
    )
    def test_cross_plane_tokens_are_partial_but_api_billing_stays_exact(
        self, attempt_planes
    ):
        from src.utils import (
            _telemetry_usage_kwargs,
            _xai_execution_attempt_receipt,
        )

        attempts = []
        for ordinal, plane in enumerate(attempt_planes, start=1):
            attempts.append(
                _xai_execution_attempt_receipt(
                    ordinal,
                    plane=plane,
                    model=(
                        "grok-composer-2.5-fast"
                        if plane == "CLI"
                        else "grok-build-0.1"
                    ),
                    outcome="completed",
                    purpose="cross-plane-recovery",
                    tokens=None if plane == "CLI" else 15,
                    cost_usd=0.0 if plane == "CLI" else 0.006,
                    usage_source=(
                        "subscription_unmetered"
                        if plane == "CLI"
                        else "provider_exact"
                    ),
                )
            )

        telemetry = _telemetry_usage_kwargs(
            plane=attempt_planes[-1],
            model=attempts[-1]["model"],
            tokens=15,
            prompt="task",
            output="answer",
            routing={"attempts": attempts},
        )

        api_attempt = next(item for item in attempts if item["plane"] == "API")
        cli_attempt = next(item for item in attempts if item["plane"] == "CLI")
        assert api_attempt["tokens"] == 15
        assert api_attempt["cost_usd"] == pytest.approx(0.006)
        assert api_attempt["billing_source"] == "xai_response_exact"
        assert "tokens" not in cli_attempt
        assert telemetry["tokens"] == 15
        assert telemetry["token_kind"] == "partial"
        assert telemetry["billing_source"] == "xai_response_exact"

    @pytest.mark.asyncio
    async def test_cli_partial_output_reaches_api_recovery_context_and_receipt(self):
        from src.utils import _GrokCLIExecutionError, orchestrate

        partial = "receipt effect-7: edited file before transport failed"
        cli_error = _GrokCLIExecutionError(
            "Grok CLI error: transport failed", partial_output=partial
        )
        with patch("src.utils.xai_api_key_configured", return_value=True), patch(
            "src.utils._select_routing_model",
            new=AsyncMock(
                side_effect=[self._cli_selection(), self._api_selection()]
            ),
        ), patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = [
                cli_error,
                ("API recovered safely", 10, 0.002, False),
            ]
            layer = await orchestrate(
                prompt="complete the task",
                enable_agentic=False,
                requested_plane="cli",
                fallback_policy="cross_plane",
            )

        assert mock_call.await_count == 2
        assert partial in mock_call.await_args_list[1].args[4]
        assert layer.finish_reason == "fallback"
        assert layer.routing_receipt["failure"]["partial_output"] == partial
        assert layer.routing_receipt["failure"]["partial_effect_possible"] is True
        assert layer.routing_receipt["attempts"][0]["partial_output"] == partial


class TestCompletionContentContract:
    reproduced_promises = (
        "I'll run a hostile Stage-1 authority-inversion review of chunk 1 "
        "(manifests + harness), mapping concrete P0/P1/P2 defects with file/line "
        "evidence and saving findings for the later consolidated verdict.",
        "Performing the chunk-1 audit from the supplied Stage-1 harness/manifest "
        "material now — concrete findings only, no deferred work.",
        "I'll peer-review PR #64 now and return a concrete verdict.",
        "Pulling PR #64 now — I'll report concrete findings shortly.",
        "I'll inspect the completion guard, then return concrete findings. "
        "I found the relevant helper in src/utils.py; next I'll read its tests.",
        "I'll inspect the completion-contract guard and its tests now and return "
        "concrete findings plus a verdict.",
        "I'll inspect the completion-contract guard and related tests directly, "
        "then return concrete findings and the smallest deterministic fix.I found "
        "the guard in `utils.py`; next I'll read the full promise/nonanswer logic "
        "and the tests that cover it.",
        "Before I answer, I'll inspect the repository and report back.",
        "I'll carefully peer-review the patch and return a verdict.",
    )
    wrapped_promises = (
        "Sure — I'll run the audit now and report back.",
        "Absolutely, I'll run the audit now and report back.",
        "Update:\nI'll run the audit now and report back.",
        "On it — I'll audit the repository and get back to you.",
        "I'll take a look and get back to you.",
        "We will run the audit and report back.",
        "I'll run it; the answer is coming shortly.",
        "I'll run it. Results: pending.",
        "I'll run it. Evidence: forthcoming.",
        "I'll run it. Issue: not checked yet.",
        "I'll run it. ```pending```",
        "I'll first inspect the repository and report back.",
        "I'll quickly inspect the repository and report back.",
        "No problem — I'll inspect the repository and report back.",
        "Sure thing — I'll inspect the repository and report back.",
        "I'll audit now. This indicates I need more time.",
        "I'll audit now. Results: still pending.",
        "I'll audit now. Answer is not yet available.",
        "I'll audit now. Evidence: I will follow up.",
        "I'll audit now. I found that I need more time.",
        "I'll audit now. I verified nothing because I have not started.",
        "I'll audit now. 0 tests passed because I have not run them.",
        "I'll audit now. Verdict: I will provide the result after I inspect it.",
    )

    @pytest.mark.parametrize(
        "content",
        (
            "",
            "   \n\t",
            *reproduced_promises,
            *wrapped_promises,
            "Plan:\n1. Inspect the repository.\n2. Run the tests.\n3. Report the results.",
            "1. Inspect the repository.\n2. Run the tests.\n3. Report back.",
            "Here's what I'll do:\n1. Inspect the repository.\n2. Run the tests.",
            "Approach:\n1. Inspect the repository.\n2. Run the tests.",
            "Steps:\n1. Inspect the repository.\n2. Run the tests.",
            "First, inspect the repository. Then run the tests. Finally, report back.",
            "1. Examine the repository.\n2. Execute the tests.\n3. Send the results.",
        ),
    )
    def test_rejects_empty_promise_and_unsolicited_plan_nonanswers(self, content):
        from src.utils import _is_nonanswer_completion

        assert _is_nonanswer_completion(content, prompt="Audit the repository") is True

    @pytest.mark.parametrize(
        "content",
        (
            'The sentence "I\'ll run the audit" is a promise, not an answer. '
            "The fix is to require evidence before accepting completion.",
            '"Performing the audit now" is not evidence. I found the bug in '
            "src/utils.py and verified the completion guard.",
            "I'll answer: 42",
            "I will use option A because it preserves the verified receipt.",
            "I will not help bypass that safety control.",
            "Let me explain: TTL belongs in every prediction input.",
            "Let me explain. TTL belongs in every prediction input.",
            "Let me explain—TTL belongs in every prediction input.",
            "Let me explain – TTL belongs in every prediction input.",
            "Let me explain - TTL belongs in every prediction input.",
            "I'll summarize — TTL belongs in every prediction input.",
            "I'll review the completion-contract result: the guard fails because "
            "it accepts an expired lease.",
            "I'll explain\nTTL belongs in every prediction input.",
            "I'll fix this:\n```python\nprint('fixed')\n```",
            "I'll review this now. Verdict: the TTL guard fails because it "
            "accepts an expired lease.",
        ),
    )
    def test_accepts_substantive_answers_and_discussion_of_promises(self, content):
        from src.utils import _is_nonanswer_completion

        assert _is_nonanswer_completion(content, prompt="Audit the repository") is False

    def test_accepts_action_plan_when_user_explicitly_requests_one(self):
        from src.utils import _is_nonanswer_completion

        content = "Plan:\n1. Inspect the repository.\n2. Run the tests.\n3. Report the results."
        assert _is_nonanswer_completion(content, prompt="Please create a plan") is False

    def test_accepts_narrative_plan_when_explicitly_requested(self):
        from src.utils import _is_nonanswer_completion

        content = "I will first inspect the repository, then run the tests."
        assert _is_nonanswer_completion(content, prompt="Please create a plan") is False

    @pytest.mark.parametrize(
        ("prompt", "content"),
        (
            (
                "Review these options",
                "I'll review each option: option A is safer because it preserves the receipt.",
            ),
            (
                "Check these claims",
                "I'll check each claim: claim one is false; claim two is true.",
            ),
        ),
    )
    def test_accepts_inline_review_answers(self, prompt, content):
        from src.utils import _is_nonanswer_completion

        assert _is_nonanswer_completion(content, prompt=prompt) is False

    def test_accepts_next_steps_when_user_asks_what_to_do(self):
        from src.utils import _is_nonanswer_completion

        content = "Next steps:\n1. Open the terminal.\n2. Run uv run pytest."
        assert _is_nonanswer_completion(content, prompt="What should I do next?") is False

    def test_accepts_direct_instructions_for_how_to_prompt(self):
        from src.utils import _is_nonanswer_completion

        content = "- Open the terminal.\n- Run uv run pytest."
        assert _is_nonanswer_completion(content, prompt="How do I run tests?") is False

    @pytest.mark.parametrize(
        "prompt",
        ("Which changes are required?", "What fixes do you recommend?"),
    )
    def test_accepts_action_list_for_advice_prompt(self, prompt):
        from src.utils import _is_nonanswer_completion

        content = "- Update the TTL guard.\n- Add missing receipt validation."
        assert _is_nonanswer_completion(content, prompt=prompt) is False


class TestHonestOutcomes:
    """Completion labels never fabricate semantic or mechanical success."""

    def _mock_store(self):
        mock_store = MagicMock()
        mock_store.get_similar_task_memories = AsyncMock(return_value=[])
        mock_store.save_telemetry = AsyncMock()
        mock_store.save_task_memory = AsyncMock()
        return mock_store

    def _mock_client(self, sample_side_effect):
        mock_chat = MagicMock()
        mock_chat.sample.side_effect = sample_side_effect
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        return mock_client

    @pytest.mark.asyncio
    async def test_agentic_final_answer_remains_unverified(self, monkeypatch):
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        resp = _make_response(content="clean answer", tool_calls=[], cost_usd=0.001)
        mock_store = self._mock_store()

        with patch("xai_sdk.Client", return_value=self._mock_client(lambda: resp)):
            layer = await orchestrate(
                prompt="hello", mode="auto", store=mock_store, dynamic_sys_prompt="sys"
            )

        assert layer.finish_reason == "final_answer"
        assert mock_store.save_telemetry.await_args.args[2] is None
        assert mock_store.save_task_memory.await_args.kwargs["success"] is None
        assert (
            mock_store.save_task_memory.await_args.kwargs["metadata"][
                "outcome_verified"
            ]
            is False
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        TestCompletionContentContract.reproduced_promises
        + TestCompletionContentContract.wrapped_promises,
    )
    async def test_agentic_promise_only_completion_is_error(self, monkeypatch, content):
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        resp = _make_response(content=content, tool_calls=[], cost_usd=0.001)
        mock_store = self._mock_store()

        with patch("xai_sdk.Client", return_value=self._mock_client(lambda: resp)):
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                store=mock_store,
                dynamic_sys_prompt="sys",
                fallback_policy="same_plane",
            )

        assert layer.generation == content
        assert layer.finish_reason == "error"
        assert mock_store.save_telemetry.await_args.args[2] == 0
        assert mock_store.save_task_memory.await_args.kwargs["success"] == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "promise",
        (
            TestCompletionContentContract.reproduced_promises[1],
            TestCompletionContentContract.reproduced_promises[2],
            TestCompletionContentContract.reproduced_promises[3],
            TestCompletionContentContract.reproduced_promises[5],
            TestCompletionContentContract.wrapped_promises[0],
            TestCompletionContentContract.wrapped_promises[6],
        ),
    )
    async def test_fast_repeated_promise_only_completion_is_error(
        self, monkeypatch, promise
    ):
        from src.utils import orchestrate

        mock_store = self._mock_store()

        with patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (promise, 10, 0.001, False)
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                store=mock_store,
                dynamic_sys_prompt="sys",
                enable_agentic=False,
            )

        assert layer.generation == (
            "Grok returned a non-answer completion twice; UniGrok "
            "rejected both responses and produced no result."
        )
        assert layer.finish_reason == "error"
        assert mock_call.await_count == 4
        assert "# Original request\nAudit the repository" in (
            mock_call.await_args_list[1].args[1]
        )
        assert "# Original request\nAudit the repository" in (
            mock_call.await_args_list[3].args[1]
        )
        assert [
            (item["plane"], item["outcome"])
            for item in layer.routing_receipt["attempts"]
        ] == [
            ("API", "nonanswer"),
            ("API", "nonanswer"),
            ("CLI", "nonanswer"),
            ("CLI", "nonanswer"),
        ]
        assert layer.routing_receipt["completion_recovery"] == {
            "attempted": True,
            "reason": "nonanswer_completion",
            "succeeded": False,
            "attempts": 1,
        }
        assert mock_store.save_telemetry.await_args.args[2] == 0
        assert mock_store.save_telemetry.await_args.args[4] == pytest.approx(0.002)
        assert layer.cost_usd == pytest.approx(0.002)
        assert mock_store.save_task_memory.await_args.kwargs["success"] == 0

    @pytest.mark.asyncio
    async def test_fast_promise_only_completion_recovers_once(self, monkeypatch):
        from src.utils import orchestrate

        mock_store = self._mock_store()
        promise = "I'll peer-review PR #64 now and return a concrete verdict."

        with patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = (
                (promise, 10, 0.001, True),
                ("Findings: the PR needs a current-main rebase.", 12, 0.002, True),
            )
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                store=mock_store,
                dynamic_sys_prompt="sys",
                enable_agentic=False,
            )

        assert layer.generation == "Findings: the PR needs a current-main rebase."
        assert layer.finish_reason == "final_answer"
        assert layer.tokens == 22
        assert layer.cost_usd == pytest.approx(0.003)
        assert mock_call.await_count == 2
        assert mock_call.await_args_list[0].kwargs["requested_model"] == (
            mock_call.await_args_list[1].kwargs["requested_model"]
        )
        assert layer.routing_receipt["completion_recovery"] == {
            "attempted": True,
            "reason": "nonanswer_completion",
            "succeeded": True,
            "attempts": 1,
        }
        assert mock_store.save_telemetry.await_args.args[2] is None
        assert mock_store.save_task_memory.await_args.kwargs["success"] is None

    @pytest.mark.asyncio
    async def test_fast_final_answer_remains_unverified(self, monkeypatch):
        from src.utils import orchestrate

        mock_store = self._mock_store()
        with patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("The audit found no blockers.", 10, 0.001, False)
            layer = await orchestrate(
                prompt="Audit the repository",
                mode="auto",
                store=mock_store,
                dynamic_sys_prompt="sys",
                enable_agentic=False,
            )

        assert layer.finish_reason == "final_answer"
        assert mock_store.save_telemetry.await_args.args[2] is None
        assert mock_store.save_task_memory.await_args.kwargs["success"] is None

    @pytest.mark.asyncio
    async def test_agentic_depth_exhaustion_records_failure(self, monkeypatch):
        """Depth-exhausted agent runs must record success=0 — previously they
        were saved with a fabricated success=1."""
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)

        def _make_tc():
            tc = MagicMock()
            tc.function.name = "__nonexistent_tool__"
            tc.function.arguments = "{}"
            return tc

        resp = _make_response(content="still working", tool_calls=[_make_tc()], cost_usd=0.001)
        mock_store = self._mock_store()

        with patch("xai_sdk.Client", return_value=self._mock_client(lambda: resp)):
            layer = await orchestrate(
                prompt="hello", mode="auto", store=mock_store, dynamic_sys_prompt="sys"
            )

        assert layer.finish_reason == "depth_exhausted"
        assert mock_store.save_telemetry.await_args.args[2] == 0
        assert mock_store.save_task_memory.await_args.kwargs["success"] == 0

    @pytest.mark.asyncio
    async def test_total_failure_records_error_finish_reason(self, monkeypatch):
        """When the fast path and the CLI fallback both fail, the layer must be
        labeled 'error' and saved with success=0 (replaces the old
        'CLI recovery failed:' string-prefix check)."""
        from src.utils import orchestrate

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        mock_store = self._mock_store()

        with patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("everything is down")
            layer = await orchestrate(
                prompt="hello",
                mode="auto",
                store=mock_store,
                dynamic_sys_prompt="sys",
                enable_agentic=False,
            )

        assert layer.finish_reason == "error"
        assert layer.generation.startswith("CLI recovery failed:")
        assert mock_store.save_task_memory.await_args.kwargs["success"] == 0

    @pytest.mark.asyncio
    async def test_cli_fallback_success_records_fallback_finish_reason(self, monkeypatch):
        """A successful CLI recovery is labeled 'fallback', not a
        clean final answer."""
        from src.utils import orchestrate

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        mock_store = self._mock_store()

        call_count = 0

        async def _flaky_plane(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("api down")
            return "cli recovered", 0, 0.0, True

        with patch("src.utils._call_plane", new=_flaky_plane):
            layer = await orchestrate(
                prompt="hello",
                mode="auto",
                store=mock_store,
                dynamic_sys_prompt="sys",
                enable_agentic=False,
            )

        assert layer.generation == "cli recovered"
        assert layer.route == "cli-fallback"
        assert layer.finish_reason == "fallback"
        assert mock_store.save_telemetry.await_args.args[2] is None
        assert mock_store.save_task_memory.await_args.kwargs["success"] is None

    def test_unknown_finish_reason_stays_unverified(self):
        from src.utils import _verified_outcome_label

        assert _verified_outcome_label("unknown") is None
        assert _verified_outcome_label("") is None
        assert _verified_outcome_label("error") == 0


class TestArchitectureUpgrades:
    """Tests covering tools manifest caching and other architectural upgrades."""

    @pytest.mark.asyncio
    async def test_get_tools_manifest_is_cached(self):
        """get_tools_manifest must cache its output for 30s and avoid listing tools repeatedly."""
        from src.utils import get_tools_manifest, _TOOLS_MANIFEST_CACHE
        
        # Clear cache first
        _TOOLS_MANIFEST_CACHE._cache.clear()

        mcp_mock = MagicMock()
        mcp_mock.list_tools = AsyncMock(return_value=[])

        # First call
        res1 = await get_tools_manifest(mcp_mock)
        assert mcp_mock.list_tools.call_count == 1

        # Second call should hit cache
        res2 = await get_tools_manifest(mcp_mock)
        assert mcp_mock.list_tools.call_count == 1
        assert res1 == res2


class TestDynamicCapacityLimits:
    """Unit tests for dynamic capacity resolution and argument injection."""

    def test_get_model_max_tokens_resolves_known_limits(self):
        from src.utils import get_model_max_tokens
        with patch("src.utils.get_xai_client", side_effect=RuntimeError("API offline")):
            # Fallback values
            assert get_model_max_tokens("grok-4.3") == 131072
            assert get_model_max_tokens("grok-composer-2.5-fast") == 131072
            assert get_model_max_tokens("unknown-model-fallback") == 131072

    def test_get_model_max_tokens_queries_api_successfully(self):
        from src.utils import get_model_max_tokens
        
        mock_model = MagicMock()
        mock_model.max_prompt_length = 262144  # Double standard limit
        
        mock_client = MagicMock()
        mock_client.models.get_language_model.return_value = mock_model
        
        with patch("src.utils.get_xai_client", return_value=mock_client):
            assert get_model_max_tokens("grok-4.3") == 262144

    def test_get_model_max_tokens_fallback_on_api_error(self):
        from src.utils import get_model_max_tokens
        
        mock_client = MagicMock()
        mock_client.models.get_language_model.side_effect = RuntimeError("API down")
        
        with patch("src.utils.get_xai_client", return_value=mock_client):
            # Fall back to hardcoded value
            assert get_model_max_tokens("grok-4.3") == 131072

    @pytest.mark.asyncio
    async def test_agentloop_scales_obs_chars_dynamically(self):
        from src.utils import AgentLoop, AgentLoopPolicy
        
        policy = AgentLoopPolicy(max_obs_chars=8000)  # Standard low limit
        loop = AgentLoop(policy=policy, dynamic_sys_prompt="System", model="grok-4.3")
        
        resp = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        
        # Mock API query to return 262144
        mock_model = MagicMock()
        mock_model.max_prompt_length = 262144
        mock_client.models.get_language_model.return_value = mock_model
        
        async def sync_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        
        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.to_thread", new=sync_to_thread):
            await loop.run("prompt")
            
        # Assert the policy limit was dynamically scaled (min 8000 tokens * 4 chars = 32000)
        assert loop.policy.max_obs_chars == 32000

    @pytest.mark.asyncio
    async def test_dispatch_one_overrides_max_chars_and_max_bytes(self):
        from src.utils import AgentLoop, AgentLoopPolicy
        
        policy = AgentLoopPolicy(max_obs_chars=50000)
        loop = AgentLoop(policy=policy, dynamic_sys_prompt="System", model="grok-4.3")
        
        mock_tc_file = MagicMock()
        mock_tc_file.function.name = "read_local_file"
        mock_tc_file.function.arguments = '{"file_path": "pyproject.toml"}'
        
        mock_tc_api = MagicMock()
        mock_tc_api.function.name = "get_file_content"
        mock_tc_api.function.arguments = '{"file_id": "file-123"}'
        
        # Mock dispatch_internal_tool
        with patch("src.utils.dispatch_internal_tool", new_callable=AsyncMock) as mock_dispatch:
            await loop._dispatch_one(mock_tc_file)
            mock_dispatch.assert_awaited_once_with(
                "read_local_file", 
                {"file_path": "pyproject.toml", "max_chars": 50000},
                30.0
            )
            
            mock_dispatch.reset_mock()
            
            await loop._dispatch_one(mock_tc_api)
            mock_dispatch.assert_awaited_once_with(
                "get_file_content", 
                {"file_id": "file-123", "max_bytes": 50000},
                30.0
            )


class TestThinkingLoop:
    """Structured-reflection thinking route (replaces GrokRouter/ThinkingKernel):
    budget ceilings honored across attempts, a failing verdict triggers a
    bounded retry, a passing verdict → final_answer, and an unavailable
    reviewer (no parse / parse error) gracefully accepts the answer."""

    def test_kernel_and_router_are_deleted(self):
        import src.utils as utils_module

        assert not hasattr(utils_module, "ThinkingKernel")
        assert not hasattr(utils_module, "GrokRouter")

    @staticmethod
    def _attempt_layer(
        generation="candidate answer",
        cost=0.01,
        tokens=10,
        finish_reason="final_answer",
        reasoning="first segment\n\n---\n\nsecond segment",
    ):
        layer = MetaLayer()
        layer.generation = generation
        layer.cost_usd = cost
        layer.tokens = tokens
        layer.finish_reason = finish_reason
        layer.reasoning = reasoning
        layer.tool_trace = [
            {"tool_name": "web_search", "tool_call_id": "c1", "success": True, "content": "evidence"}
        ]
        return layer

    @pytest.mark.asyncio
    async def test_passing_verdict_is_final_answer(self):
        from src.utils import run_thinking_loop

        attempts = []

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            attempts.append(prompt)
            return TestThinkingLoop._attempt_layer(generation="good answer")

        mock_reflect = AsyncMock(
            return_value=(ReflectionVerdict(status="pass"), 5, 0.001)
        )
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            layer = await run_thinking_loop("Implement code with tests")

        assert len(attempts) == 1
        assert layer.generation == "good answer"
        assert layer.finish_reason == "final_answer"
        assert layer.route == "thinking"
        assert layer.plane == "API"
        # Plan = first attempt's opening reasoning segment.
        assert layer.plan == "first segment"
        assert "verdict=pass" in layer.reflection

    @pytest.mark.asyncio
    async def test_thinking_counts_agent_sample_and_reflection_parse_calls(self):
        from src.utils import run_thinking_loop

        attempts = []

        def record_attempt(**attempt):
            attempts.append(attempt)

        candidate = _make_response(
            content="complete result", tool_calls=[], cost_usd=0.01
        )
        agent_chat = MagicMock()
        agent_chat.sample.return_value = candidate
        agent_chat.append = MagicMock()

        review_response = MagicMock()
        review_response.usage.prompt_tokens = 2
        review_response.usage.completion_tokens = 1
        review_response.cost_usd = 0.002
        reflection_chat = MagicMock()
        reflection_chat.parse.return_value = (
            review_response,
            ReflectionVerdict(status="pass"),
        )
        reflection_chat.append = MagicMock()

        client = MagicMock()
        client.chat.create.side_effect = [agent_chat, reflection_chat]
        with patch("src.utils.get_xai_client", return_value=client), patch(
            "src.utils.resolve_model", new=AsyncMock(return_value="grok-4.3")
        ), patch("src.utils.get_model_max_tokens", return_value=131072):
            layer = await run_thinking_loop(
                "solve this",
                model="grok-4.3",
                attempt_recorder=record_attempt,
                defer_telemetry=True,
            )

        assert layer.finish_reason == "final_answer"
        assert agent_chat.sample.call_count == 1
        assert reflection_chat.parse.call_count == 1
        assert len(attempts) == 2
        assert [attempt["purpose"] for attempt in attempts] == [
            "thinking:depth 1",
            "reflection",
        ]
        assert [attempt["cost_usd"] for attempt in attempts] == [
            pytest.approx(0.01),
            pytest.approx(0.002),
        ]

    @pytest.mark.asyncio
    async def test_thinking_history_uses_injected_store(self):
        from src.utils import run_thinking_loop

        injected_store = MagicMock(name="injected_store")
        captured = {}

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            captured["history"] = history
            return TestThinkingLoop._attempt_layer()

        expected_history = [{"role": "user", "content": "prior turn"}]
        history_loader = AsyncMock(return_value=expected_history)
        with patch("src.utils.load_history", new=history_loader), patch(
            "src.utils.AgentLoop.run", new=fake_run
        ), patch(
            "src.utils._reflect_on_answer",
            new=AsyncMock(return_value=(None, 0, 0.0)),
        ):
            await run_thinking_loop(
                "continue",
                session="grok-owned-session",
                store=injected_store,
                defer_telemetry=True,
            )

        history_loader.assert_awaited_once_with(
            "grok-owned-session", injected_store
        )
        assert captured["history"] == expected_history

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "promise",
        (
            TestCompletionContentContract.reproduced_promises[0],
            TestCompletionContentContract.reproduced_promises[5],
            TestCompletionContentContract.wrapped_promises[6],
        ),
    )
    async def test_promise_only_attempt_is_error_before_review(self, promise):
        from src.utils import run_thinking_loop

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            return TestThinkingLoop._attempt_layer(generation=promise)

        mock_reflect = AsyncMock(
            return_value=(ReflectionVerdict(status="pass"), 5, 0.001)
        )
        mock_store = MagicMock()
        mock_store.save_telemetry = AsyncMock()
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            layer = await run_thinking_loop("Audit the repository", store=mock_store)

        mock_reflect.assert_not_awaited()
        assert layer.generation == promise
        assert layer.finish_reason == "error"
        assert mock_store.save_telemetry.await_args.args[2] == 0

    @pytest.mark.asyncio
    async def test_failing_verdict_triggers_bounded_retry(self):
        from src.utils import run_thinking_loop

        attempts = []

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            attempts.append(prompt)
            return TestThinkingLoop._attempt_layer(generation=f"attempt {len(attempts)}")

        mock_reflect = AsyncMock(
            return_value=(
                ReflectionVerdict(status="fail", issues=["missing test"], next_action="add a test"),
                5,
                0.001,
            )
        )
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            layer = await run_thinking_loop(
                "Implement code with tests", max_reflections=1, global_budget_usd=1.0
            )

        # max_reflections=1 → initial attempt + exactly one reviewer-driven retry.
        assert len(attempts) == 2
        # The retry prompt carries the reviewer feedback.
        assert "Reviewer found: missing test" in attempts[1]
        assert "add a test" in attempts[1]
        # Retries exhausted with the verdict still failing → honest label.
        assert layer.finish_reason == "depth_exhausted"
        assert layer.generation == "attempt 2"
        assert "verdict=fail" in layer.reflection
        assert "missing test" in layer.reflection

    @pytest.mark.asyncio
    async def test_retry_keeps_input_messages_conversation(self):
        """Reviewer-driven retries must not drop the caller's conversation:
        the retry re-sends the full input_messages with the reviewer
        correction appended as a trailing user turn (previously retries
        passed input_messages=None and lost every prior turn)."""
        from src.utils import run_thinking_loop

        conversation = [
            {"role": "user", "content": "write the parser"},
            {"role": "assistant", "content": "def parse(): ..."},
            {"role": "user", "content": "now fix the bug in it"},
        ]
        calls = []

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            calls.append({"prompt": prompt, "input_messages": input_messages})
            return TestThinkingLoop._attempt_layer(generation=f"attempt {len(calls)}")

        mock_reflect = AsyncMock(
            return_value=(
                ReflectionVerdict(status="fail", issues=["missing test"], next_action="add a test"),
                5,
                0.001,
            )
        )
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            await run_thinking_loop(
                "now fix the bug in it",
                input_messages=conversation,
                max_reflections=1,
                global_budget_usd=1.0,
            )

        assert len(calls) == 2
        # Attempt 1: the caller's transcript verbatim.
        assert calls[0]["input_messages"] == conversation
        # Attempt 2: full conversation retained, correction as a new user turn.
        retry_messages = calls[1]["input_messages"]
        assert retry_messages is not None
        assert retry_messages[: len(conversation)] == conversation
        assert retry_messages[-1]["role"] == "user"
        assert "Reviewer found: missing test" in retry_messages[-1]["content"]
        assert "add a test" in retry_messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_budget_exhaustion_stops_before_reflection(self):
        from src.utils import run_thinking_loop

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            return TestThinkingLoop._attempt_layer(
                generation="partial answer", cost=0.60, finish_reason="budget_exhausted"
            )

        mock_reflect = AsyncMock()
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            layer = await run_thinking_loop("big task", global_budget_usd=0.50)

        mock_reflect.assert_not_awaited()
        assert layer.finish_reason == "budget_exhausted"
        assert layer.generation == "partial answer"
        assert "Budget" in layer.reflection

    @pytest.mark.asyncio
    async def test_reflection_cost_counts_toward_shared_budget(self):
        from src.utils import run_thinking_loop

        attempts = []

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            attempts.append(prompt)
            return TestThinkingLoop._attempt_layer(cost=0.30)

        # Reviewer fails the answer AND its cost pushes the total over budget:
        # the loop must stop instead of paying for another attempt.
        mock_reflect = AsyncMock(
            return_value=(
                ReflectionVerdict(status="fail", issues=["incomplete"]),
                5,
                0.30,
            )
        )
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            layer = await run_thinking_loop(
                "big task", max_reflections=3, global_budget_usd=0.50
            )

        assert len(attempts) == 1
        assert layer.finish_reason == "budget_exhausted"
        assert layer.cost_usd == pytest.approx(0.60)
        assert "Budget" in layer.reflection

    @pytest.mark.asyncio
    async def test_reviewer_unavailable_accepts_answer(self):
        """Parse failure / missing capability → graceful accept, never string
        scanning: the attempt's own finish_reason stands."""
        from src.utils import run_thinking_loop

        attempts = []

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            attempts.append(prompt)
            return TestThinkingLoop._attempt_layer(generation="unreviewed answer")

        mock_reflect = AsyncMock(return_value=(None, 0, 0.0))
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            layer = await run_thinking_loop("task", max_reflections=2)

        assert len(attempts) == 1
        assert layer.generation == "unreviewed answer"
        assert layer.finish_reason == "final_answer"
        assert "reviewer unavailable" in layer.reflection


class TestReflectOnAnswer:
    """_reflect_on_answer builds a dedicated TOOL-FREE chat and parses a
    ReflectionVerdict via chat.parse(); any failure returns None (accept)."""

    @staticmethod
    def _mock_client(chat):
        client = MagicMock()
        client.chat.create.return_value = chat
        return client

    @pytest.mark.asyncio
    async def test_reflection_chat_is_tool_free_and_parses_verdict(self):
        from src.utils import _reflect_on_answer

        resp = MagicMock()
        resp.usage.prompt_tokens = 7
        resp.usage.completion_tokens = 3
        resp.cost_usd = 0.002
        verdict_obj = ReflectionVerdict(status="fail", issues=["bug"], next_action="fix it")

        mock_chat = MagicMock()
        mock_chat.parse.return_value = (resp, verdict_obj)
        client = self._mock_client(mock_chat)

        trace = [{"tool_name": "run_local_tests", "success": True, "content": "1 failed"}]
        with patch("src.utils.get_xai_client", return_value=client):
            verdict, tokens, cost = await _reflect_on_answer(
                "original request", "candidate", trace, "grok-4.3"
            )

        # Dedicated TOOL-FREE chat: no tools kwarg may ever reach chat.create.
        assert client.chat.create.call_args.kwargs == {"model": "grok-4.3"}
        mock_chat.parse.assert_called_once_with(ReflectionVerdict)
        # The review prompt carries the request, the answer, and tool evidence.
        appended = [str(c.args[0]) for c in mock_chat.append.call_args_list]
        assert any("original request" in text and "candidate" in text for text in appended)
        assert any("run_local_tests" in text for text in appended)
        assert verdict == verdict_obj
        assert tokens == 10
        assert cost == 0.002

    @pytest.mark.asyncio
    async def test_parse_error_returns_none(self):
        from src.utils import _reflect_on_answer

        mock_chat = MagicMock()
        mock_chat.parse.side_effect = ValueError("model emitted a tool call")
        client = self._mock_client(mock_chat)

        with patch("src.utils.get_xai_client", return_value=client):
            verdict, tokens, cost = await _reflect_on_answer("req", "ans", [], "grok-4.3")

        assert verdict is None
        assert tokens == 0
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_missing_parse_capability_returns_none(self):
        from src.utils import _reflect_on_answer

        # A chat surface without parse() — the capability gate must accept.
        mock_chat = MagicMock(spec=["append"])
        client = self._mock_client(mock_chat)

        with patch("src.utils.get_xai_client", return_value=client):
            verdict, tokens, cost = await _reflect_on_answer("req", "ans", [], "grok-4.3")

        assert verdict is None
        assert tokens == 0
        assert cost == 0.0


class TestOrchestrateThinkingRoute:
    """orchestrate(thinking_mode=True) runs the structured-reflection loop and
    labels the MetaLayer with route='thinking'."""

    @pytest.mark.asyncio
    async def test_thinking_mode_runs_agentloop_plus_reflection(self, monkeypatch):
        from src.utils import DEFAULT_PLANNING_MODEL, orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        seen_models = []

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            seen_models.append(self.model)
            return TestThinkingLoop._attempt_layer(generation="deep answer")

        mock_reflect = AsyncMock(
            return_value=(ReflectionVerdict(status="pass"), 5, 0.001)
        )
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            layer = await orchestrate(
                prompt="hardest task",
                thinking_mode=True,
                dynamic_sys_prompt="sys",
            )

        mock_call.assert_not_awaited()
        assert seen_models == [DEFAULT_PLANNING_MODEL]
        mock_reflect.assert_awaited_once()
        assert layer.route == "thinking"
        assert layer.plane == "API"
        assert layer.generation == "deep answer"
        assert layer.finish_reason == "final_answer"

    @pytest.mark.asyncio
    async def test_thinking_route_failure_falls_back_with_fallback_label(self, monkeypatch):
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        with patch("src.utils.run_thinking_loop", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("fast answer", 10, 0.001, False)
            layer = await orchestrate(
                prompt="hardest task",
                thinking_mode=True,
                enable_agentic=False,
                dynamic_sys_prompt="sys",
                fallback_policy="same_plane",
            )

        assert layer.route == "fast"
        assert layer.generation == "fast answer"
        assert layer.finish_reason == "fallback"

    @pytest.mark.asyncio
    async def test_thinking_telemetry_row_carries_caller(self):
        """Regression (round-3 review): the thinking route is the most
        expensive one — its telemetry row must carry the caller so per-caller
        budgets and /metrics segmentation count thinking-mode spend."""
        from src.utils import run_thinking_loop

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            return TestThinkingLoop._attempt_layer()

        mock_reflect = AsyncMock(
            return_value=(ReflectionVerdict(status="pass"), 5, 0.001)
        )
        fake_store = MagicMock()
        fake_store.save_telemetry = AsyncMock()
        with patch("src.utils.AgentLoop.run", new=fake_run), \
             patch("src.utils._reflect_on_answer", new=mock_reflect):
            await run_thinking_loop(
                "attribute this spend", store=fake_store, caller="claude-code"
            )

        fake_store.save_telemetry.assert_awaited_once()
        assert fake_store.save_telemetry.await_args.kwargs["caller"] == "claude-code"

    @pytest.mark.asyncio
    async def test_direct_thinking_telemetry_owns_physical_attempt_receipts(self):
        from src.utils import run_thinking_loop

        async def fake_run(self, prompt, session=None, history=None, input_messages=None):
            self.attempt_recorder(
                plane="API",
                model=self.model,
                outcome="completed",
                purpose="thinking:depth 1/8",
                tokens=9,
                cost_usd=0.003,
                usage_source="provider_exact",
            )
            return TestThinkingLoop._attempt_layer()

        fake_store = MagicMock()
        fake_store.save_telemetry = AsyncMock()
        with patch("src.utils.AgentLoop.run", new=fake_run), patch(
            "src.utils._reflect_on_answer",
            new=AsyncMock(return_value=(None, 0, 0.0)),
        ):
            layer = await run_thinking_loop("attribute every call", store=fake_store)

        attempts = layer.routing_receipt["attempts"]
        assert len(attempts) == 1
        assert attempts[0]["billing_source"] == "xai_response_exact"
        assert fake_store.save_telemetry.await_args.kwargs["routing"] == (
            layer.routing_receipt
        )

    @pytest.mark.asyncio
    async def test_orchestrate_threads_caller_into_thinking_route(self, monkeypatch):
        """orchestrate resolves the caller once and must hand it to
        run_thinking_loop like it does on the agentic/fast telemetry saves."""
        from src.utils import orchestrate

        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        captured = {}

        async def fake_thinking(prompt, **kwargs):
            captured.update(kwargs)
            return TestThinkingLoop._attempt_layer()

        with patch("src.utils.run_thinking_loop", new=fake_thinking):
            await orchestrate(
                prompt="hardest task",
                thinking_mode=True,
                dynamic_sys_prompt="sys",
                caller="claude-code",
            )

        assert captured["caller"] == "claude-code"

    @pytest.mark.asyncio
    async def test_orchestrate_persists_final_thinking_receipt_exactly_once(self):
        from src.utils import orchestrate

        captured = {}

        async def fake_thinking(prompt, **kwargs):
            captured.update(kwargs)
            recorder = kwargs["attempt_recorder"]
            recorder(
                plane="API",
                model="grok-build-0.1",
                outcome="completed",
                purpose="thinking:depth 1",
                tokens=11,
                cost_usd=0.01,
            )
            recorder(
                plane="API",
                model="grok-build-0.1",
                outcome="completed",
                purpose="reflection",
                tokens=3,
                cost_usd=0.002,
            )
            return MetaLayer(
                generation="verified candidate",
                finish_reason="final_answer",
                plane="API",
                route="thinking",
                tokens=14,
                cost_usd=0.012,
                routing_receipt=dict(kwargs["routing_receipt"]),
            )

        selection = (
            "grok-build-0.1",
            "reasoning",
            {
                "route_class": "reasoning",
                "resolved_model": "grok-build-0.1",
                "catalog": {"source": "xai_api_live", "fallback": False},
            },
            True,
        )
        store = MagicMock()
        store.get_similar_task_memories = AsyncMock(return_value=[])
        store.save_telemetry = AsyncMock()
        store.save_task_memory = AsyncMock()
        with patch(
            "src.utils._select_routing_model", new=AsyncMock(return_value=selection)
        ), patch("src.utils.run_thinking_loop", new=fake_thinking):
            layer = await orchestrate(
                prompt="hardest task",
                thinking_mode=True,
                store=store,
                requested_plane="api",
                fallback_policy="same_plane",
            )

        assert captured["defer_telemetry"] is True
        assert len(layer.routing_receipt["attempts"]) == 2
        store.save_telemetry.assert_awaited_once()
        assert (
            store.save_telemetry.await_args.kwargs["routing"]
            == layer.routing_receipt
        )


# ─────────────────────────────────────────────────────────────────────────────
# Now#5 — Error classification, circuit breaker, run_blocking resilience
# ─────────────────────────────────────────────────────────────────────────────

class _FakeGrpcError(Exception):
    """Mimics grpc.RpcError: exposes .code() returning a StatusCode-like object."""

    def __init__(self, code_name: str, message: str = "grpc failure"):
        super().__init__(message)
        self._code_name = code_name

    def code(self):
        return SimpleNamespace(name=self._code_name)


class TestXaiErrorClassification:
    """classify_xai_error: retryable (429/5xx/transient) vs fatal (4xx/validation),
    defaulting unknown errors to retryable."""

    def test_grpc_codes_classified(self):
        from src.utils import classify_xai_error

        assert classify_xai_error(_FakeGrpcError("UNAVAILABLE")) == "retryable"
        assert classify_xai_error(_FakeGrpcError("RESOURCE_EXHAUSTED")) == "retryable"
        assert classify_xai_error(_FakeGrpcError("DEADLINE_EXCEEDED")) == "retryable"
        assert classify_xai_error(_FakeGrpcError("INTERNAL")) == "retryable"
        assert classify_xai_error(_FakeGrpcError("UNAUTHENTICATED")) == "fatal"
        assert classify_xai_error(_FakeGrpcError("PERMISSION_DENIED")) == "fatal"
        assert classify_xai_error(_FakeGrpcError("INVALID_ARGUMENT")) == "fatal"
        assert classify_xai_error(_FakeGrpcError("NOT_FOUND")) == "fatal"

    def test_http_status_attribute_classified(self):
        from src.utils import classify_xai_error

        def _exc(status):
            exc = Exception("boom")
            exc.status_code = status
            return exc

        assert classify_xai_error(_exc(429)) == "retryable"
        assert classify_xai_error(_exc(500)) == "retryable"
        assert classify_xai_error(_exc(503)) == "retryable"
        assert classify_xai_error(_exc(400)) == "fatal"
        assert classify_xai_error(_exc(401)) == "fatal"
        assert classify_xai_error(_exc(403)) == "fatal"
        assert classify_xai_error(_exc(404)) == "fatal"

    def test_type_and_default_classification(self):
        from src.utils import CircuitBreakerOpenError, classify_xai_error

        assert classify_xai_error(ConnectionError("unreachable")) == "retryable"
        assert classify_xai_error(TimeoutError("timed out")) == "retryable"
        assert classify_xai_error(ValueError("bad payload")) == "fatal"
        assert classify_xai_error(CircuitBreakerOpenError("open")) == "fatal"
        # Unknown errors default to retryable
        assert classify_xai_error(Exception("mystery upstream hiccup")) == "retryable"
        # Message-based fatal markers
        assert classify_xai_error(Exception("Invalid API key provided")) == "fatal"

    def test_retry_after_hint_extraction(self):
        from src.utils import _retry_after_hint

        exc = Exception("rate limited")
        exc.retry_after = 7
        assert _retry_after_hint(exc) == 7.0

        exc2 = Exception("rate limited")
        exc2.response = SimpleNamespace(headers={"Retry-After": "12"})
        assert _retry_after_hint(exc2) == 12.0

        # Clamped to 60s, and absent hints return None
        exc3 = Exception("rate limited")
        exc3.retry_after = 3600
        assert _retry_after_hint(exc3) == 60.0
        assert _retry_after_hint(Exception("nothing")) is None


class TestCircuitBreaker:
    """Per-model breaker: opens after N consecutive failures, fails fast for a
    cool-down, half-opens for a probe, and resets on success."""

    def test_breaker_opens_after_threshold(self, monkeypatch):
        from src.utils import (
            CircuitBreakerOpenError,
            check_circuit_breaker,
            get_circuit_breaker_state,
            record_xai_failure,
        )

        monkeypatch.setenv("UNIGROK_BREAKER_THRESHOLD", "3")
        check_circuit_breaker("model-x")  # closed — no-op
        for _ in range(3):
            record_xai_failure("model-x")

        with pytest.raises(CircuitBreakerOpenError, match="model-x"):
            check_circuit_breaker("model-x")

        state = get_circuit_breaker_state()["model-x"]
        assert state["open"] is True
        assert state["consecutive_failures"] == 3
        assert state["trips"] == 1
        assert state["cooldown_remaining_sec"] > 0

    def test_breaker_success_resets(self, monkeypatch):
        from src.utils import (
            check_circuit_breaker,
            get_circuit_breaker_state,
            record_xai_failure,
            record_xai_success,
        )

        monkeypatch.setenv("UNIGROK_BREAKER_THRESHOLD", "3")
        record_xai_failure("model-y")
        record_xai_failure("model-y")
        record_xai_success("model-y")
        check_circuit_breaker("model-y")  # must not raise
        assert get_circuit_breaker_state()["model-y"]["consecutive_failures"] == 0

    def test_breaker_half_opens_after_cooldown(self, monkeypatch):
        import src.utils as utils_module
        from src.utils import check_circuit_breaker, get_circuit_breaker_state, record_xai_failure

        monkeypatch.setenv("UNIGROK_BREAKER_THRESHOLD", "1")
        record_xai_failure("model-z")
        # Rewind opened_at past the cool-down → next check half-opens (probe allowed)
        utils_module._BREAKER_STATE["model-z"]["opened_at"] = time.time() - 10_000
        check_circuit_breaker("model-z")  # must not raise
        assert get_circuit_breaker_state()["model-z"]["open"] is False
        # A failing probe re-opens the breaker
        record_xai_failure("model-z")
        assert get_circuit_breaker_state()["model-z"]["open"] is True
        assert get_circuit_breaker_state()["model-z"]["trips"] == 2

    @pytest.mark.asyncio
    async def test_call_plane_fails_fast_when_breaker_open(self, monkeypatch):
        """_call_plane's API branch must not touch the SDK while the breaker
        for its model is open."""
        from src.utils import CircuitBreakerOpenError, _call_plane, record_xai_failure

        monkeypatch.setenv("UNIGROK_BREAKER_THRESHOLD", "1")
        record_xai_failure("grok-4.3")

        with patch("src.utils.get_xai_client") as mock_get_client:
            with pytest.raises(CircuitBreakerOpenError):
                await _call_plane(
                    "reasoning", "hello", None, None, "sys", requested_model="grok-4.3"
                )

        mock_get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_agentloop_fatal_error_raises_without_retries(self):
        """Fatal (auth) errors must raise immediately instead of burning the
        retry budget on a credential that cannot heal itself."""

        class AuthError(Exception):
            status_code = 401

        call_count = 0

        def _boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise AuthError("bad credentials")

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _boom
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
        )

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(AuthError, match="bad credentials"):
                await loop.run("test prompt")

        assert call_count == 1, f"Fatal errors must not retry, got {call_count} attempts"
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agentloop_honors_retry_after_hint(self):
        """A 429 exposing retry_after must drive the backoff wait instead of
        the default exponential schedule."""

        class RateLimited(Exception):
            status_code = 429
            retry_after = 7

        resp = _make_response(content="recovered", tool_calls=[], cost_usd=0.001)
        call_count = 0

        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimited("slow down")
            return resp

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
        )

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            layer = await loop.run("test prompt")

        assert layer.generation == "recovered"
        assert mock_sleep.await_args.args[0] == 7.0

    @pytest.mark.asyncio
    async def test_agentloop_sample_failures_trip_breaker(self, monkeypatch):
        """Consecutive sample failures must open the breaker so the next
        attempt fails fast instead of hammering a broken upstream."""
        from src.utils import CircuitBreakerOpenError, get_circuit_breaker_state

        monkeypatch.setenv("UNIGROK_BREAKER_THRESHOLD", "2")

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = ConnectionError("xAI down")
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
        )

        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            # Failures 1+2 open the breaker; the third attempt fails fast.
            with pytest.raises(CircuitBreakerOpenError):
                await loop.run("test prompt")

        assert mock_chat.sample.call_count == 2
        state = get_circuit_breaker_state()["grok-4.3"]
        assert state["open"] is True
        assert state["trips"] == 1


class TestRunBlockingResilience:
    """run_blocking must survive hung callables; get_xai_client must be
    thread-safe."""

    @pytest.mark.asyncio
    async def test_run_blocking_timeout_does_not_exhaust_workers(self):
        """A timed-out call must not permanently occupy a shared-pool worker —
        8 hung calls previously deadlocked every SDK bridge in the server."""
        from src.utils import run_blocking

        hang = threading.Event()

        async def _timed_hang():
            with pytest.raises(asyncio.TimeoutError):
                await run_blocking(hang.wait, timeout=0.05)

        try:
            # Strand more calls than the shared pool has workers (8).
            await asyncio.gather(*[_timed_hang() for _ in range(10)])
            # A fresh call must still complete promptly.
            result = await asyncio.wait_for(
                run_blocking(lambda: "alive", timeout=2.0), timeout=2.0
            )
            assert result == "alive"
        finally:
            hang.set()

    def test_get_xai_client_thread_safe_single_instance(self):
        """Concurrent first calls must construct exactly one Client — the old
        unguarded check-then-set leaked duplicates from executor threads."""
        import src.utils as utils_module

        constructed = []

        class SlowClient:
            def __init__(self, api_key=None, management_api_key=None):
                time.sleep(0.02)
                constructed.append(self)

        with patch("xai_sdk.Client", new=SlowClient):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                clients = list(pool.map(lambda _: utils_module.get_xai_client(), range(8)))

        assert len(constructed) == 1
        assert all(client is clients[0] for client in clients)


class TestTimedThreadCap:
    """Timed run_blocking calls are counted and capped: a storm of hung calls
    fails fast with RuntimeError instead of spawning unbounded daemon threads,
    and stranded threads keep occupying capacity until they truly finish."""

    async def _drain_timed_threads(self):
        from src.utils import get_runtime_stats

        for _ in range(150):
            if get_runtime_stats()["timed_threads_in_flight"] == 0:
                return
            await asyncio.sleep(0.02)
        pytest.fail("timed threads from earlier calls never drained")

    @pytest.mark.asyncio
    async def test_capacity_exhausted_fails_fast_then_recovers(self, monkeypatch):
        from src.utils import get_runtime_stats, run_blocking

        monkeypatch.setenv("UNIGROK_MAX_TIMED_THREADS", "2")
        await self._drain_timed_threads()

        hang = threading.Event()
        try:
            # Strand two timed calls: they time out but their threads stay
            # in flight (and counted) until the callable actually returns.
            for _ in range(2):
                with pytest.raises(asyncio.TimeoutError):
                    await run_blocking(hang.wait, timeout=0.05)
            assert get_runtime_stats()["timed_threads_in_flight"] == 2

            # At capacity the call must fail fast — no third thread spawned.
            with pytest.raises(RuntimeError, match="timed-call capacity exhausted"):
                await run_blocking(lambda: "never-runs", timeout=1.0)
            assert get_runtime_stats()["timed_threads_in_flight"] == 2
        finally:
            hang.set()

        # Once the stranded callables finish, capacity frees up again.
        await self._drain_timed_threads()
        result = await run_blocking(lambda: "alive", timeout=1.0)
        assert result == "alive"
        assert get_runtime_stats()["timed_threads_peak"] >= 2

    @pytest.mark.asyncio
    async def test_counter_decrements_on_normal_completion(self):
        from src.utils import get_runtime_stats, run_blocking

        await self._drain_timed_threads()
        assert await run_blocking(lambda: 42, timeout=1.0) == 42
        await self._drain_timed_threads()
        stats = get_runtime_stats()
        assert stats["timed_threads_in_flight"] == 0
        assert stats["timed_threads_peak"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Next#9 — Real memory: stable partition key, tool-trace persistence + replay,
# atomic history save
# ─────────────────────────────────────────────────────────────────────────────

class TestStableContextId:
    """The task-memory partition key must be stable across requests — the old
    ms-timestamp prefix made the context-match retrieval bonus unreachable."""

    @pytest.mark.asyncio
    async def test_context_id_is_stable_across_requests(self):
        from src.utils import get_dynamic_context, git_cache

        try:
            git_cache.clear()
            _, _, cid1 = await get_dynamic_context()
            git_cache.clear()
            _, _, cid2 = await get_dynamic_context()
        finally:
            git_cache.clear()

        assert cid1 == cid2, "identical workspace state must yield the same context_id"
        assert not re.search(r"ctx-\d{10,}", cid1), "context_id must not embed a timestamp"

    @pytest.mark.asyncio
    async def test_git_rename_status_uses_post_arrow_path(self, tmp_path, monkeypatch):
        """Quick win 12: 'R old -> new' porcelain lines must resolve to the
        post-arrow path instead of the bogus 'old -> new' string."""
        from src.utils import get_dynamic_context, git_cache

        monkeypatch.setattr(PathResolver, "get_workspace_root", staticmethod(lambda: tmp_path))
        (tmp_path / "new_name.py").write_text("print('hi')\n", encoding="utf-8")

        class FakeProc:
            def __init__(self, out):
                self._out = out
                self.returncode = 0

            async def communicate(self):
                return self._out, b""

        def fake_exec(*cmd, **kwargs):
            async def _spawn():
                if "status" in cmd:
                    return FakeProc(b"R  old_name.py -> new_name.py\n")
                if "--abbrev-ref" in cmd:
                    return FakeProc(b"feature/renames\n")
                return FakeProc(b"abcdef123456\n")
            return _spawn()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        try:
            git_cache.clear()
            context, injected, context_id = await get_dynamic_context()
        finally:
            git_cache.clear()

        assert injected is True
        assert "new_name.py" in context
        assert "old_name.py ->" not in context
        # Branch is part of the stable partition key (sanitized: / → -)
        assert "feature-renames" in context_id


class TestToolTraceMemory:
    """Tool observations persist in message metadata and replay on the next
    turn so multi-step work can continue across turns."""

    @pytest.fixture
    async def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_chats_dir", classmethod(lambda cls: tmp_path))
        s = GrokSessionStore()
        yield s
        await s.close()

    def test_format_tool_trace_block_compact_and_bounded(self):
        from src.utils import format_tool_trace_block

        trace = [{"tool_name": "read_local_file", "success": True, "content": "x" * 1000}]
        block = format_tool_trace_block(trace)
        assert "read_local_file (ok)" in block
        assert "[...truncated]" in block
        assert "Tool observations from an earlier turn" in block

        failed = format_tool_trace_block([{"tool_name": "git_diff", "success": False, "content": "boom"}])
        assert "git_diff (error): boom" in failed

        assert format_tool_trace_block([]) == ""
        assert format_tool_trace_block(["not-a-dict"]) == ""

    @pytest.mark.asyncio
    async def test_agentloop_populates_tool_trace(self):
        """AgentLoop.run must record every observation on layer.tool_trace with
        content truncated to 2000 chars."""
        async def big_tool() -> str:
            return "y" * 5000

        register_internal_tool("__trace_tool__", big_tool)

        tc = MagicMock()
        tc.id = "call-t1"
        tc.function.name = "__trace_tool__"
        tc.function.arguments = "{}"
        resp_tools = _make_response(content="", tool_calls=[tc], cost_usd=0.001)
        resp_final = _make_response(content="done", tool_calls=[], cost_usd=0.001)

        call_count = 0

        def _sample(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return resp_tools if call_count == 1 else resp_final

        mock_chat = MagicMock()
        mock_chat.sample.side_effect = _sample
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=3),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
        )

        try:
            with patch("xai_sdk.Client", return_value=mock_client):
                layer = await loop.run("use the tool")
        finally:
            _INTERNAL_TOOL_REGISTRY.pop("__trace_tool__", None)

        assert len(layer.tool_trace) == 1
        entry = layer.tool_trace[0]
        assert entry["tool_name"] == "__trace_tool__"
        assert entry["tool_call_id"] == "call-t1"
        assert entry["success"] is True
        assert len(entry["content"]) <= 2000

    @pytest.mark.asyncio
    async def test_append_and_save_history_caps_and_roundtrips_tool_trace(self, store):
        trace = [{"tool_name": f"t{i}", "success": True, "content": "c"} for i in range(25)]
        await append_and_save_history(
            "sess-trace", [], "question", "answer", store,
            metadata={"model": "m", "tool_trace": trace},
        )

        loaded = await load_history("sess-trace", store)
        assert loaded[1]["role"] == "assistant"
        saved_trace = loaded[1]["metadata"]["tool_trace"]
        assert len(saved_trace) == 20, "persisted tool traces are capped at 20 entries"
        assert saved_trace[0]["tool_name"] == "t0"

    @pytest.mark.asyncio
    async def test_agentloop_replays_tool_trace_from_history(self):
        """A history message carrying a tool_trace must be replayed as a compact
        assistant context block BEFORE the assistant reply, so the model sees
        what its tools observed last turn."""
        history = [
            {"role": "user", "content": "read the config file"},
            {
                "role": "assistant",
                "content": "done reading",
                "metadata": {
                    "tool_trace": [
                        {"tool_name": "read_local_file", "success": True, "content": "print('hello')"}
                    ]
                },
            },
        ]

        resp = _make_response(content="continuing", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
        )

        mock_assistant = MagicMock(side_effect=lambda *a: ("assistant", a))
        with patch("xai_sdk.Client", return_value=mock_client), \
             patch("xai_sdk.chat.assistant", new=mock_assistant):
            layer = await loop.run("continue the work", history=history)

        assert layer.generation == "continuing"
        assistant_texts = [call.args[0] for call in mock_assistant.call_args_list]
        trace_indexes = [
            i for i, text in enumerate(assistant_texts)
            if "Tool observations from an earlier turn" in text and "read_local_file (ok)" in text
        ]
        reply_indexes = [i for i, text in enumerate(assistant_texts) if text == "done reading"]
        assert trace_indexes, "tool trace context block was not replayed"
        assert reply_indexes, "assistant reply was not replayed"
        assert trace_indexes[0] < reply_indexes[0], "trace block must precede the reply"

    @pytest.mark.asyncio
    async def test_run_agent_turn_persists_tool_trace_metadata(self, monkeypatch):
        """run_agent_turn must include layer.tool_trace in the saved metadata."""
        trace = [{"tool_name": "git_diff", "tool_call_id": "c1", "success": True, "content": "diff"}]
        monkeypatch.setattr(
            "src.utils.get_dynamic_context",
            AsyncMock(return_value=("system context", True, "ctx-stable")),
        )
        monkeypatch.setattr(
            "src.utils.orchestrate",
            AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-stable", tool_trace=trace)),
        )
        monkeypatch.setattr("src.utils.load_history", AsyncMock(return_value=[]))
        mock_append = AsyncMock()
        monkeypatch.setattr("src.utils.append_and_save_history", mock_append)
        monkeypatch.setattr("src.utils.store.save_session", AsyncMock())

        from src.utils import run_agent_turn
        await run_agent_turn(prompt="do work", session="s-trace")

        metadata = mock_append.await_args.kwargs["metadata"]
        assert metadata["tool_trace"] == trace


class TestAtomicSaveHistory:
    """replace_messages wraps delete+insert in one transaction; save_history
    uses it instead of the crash-lossy delete-then-reinsert flow."""

    @pytest.fixture
    async def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_chats_dir", classmethod(lambda cls: tmp_path))
        s = GrokSessionStore()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_replace_messages_roundtrip_preserves_session_row(self, store):
        await store.save_session("sess-atomic", cli_session_id="cli-1", model="grok-4.3")
        await store.save_message("sess-atomic", "user", "old message")

        await store.replace_messages("sess-atomic", [
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer", "metadata": {"k": 1}},
        ])

        messages = await store.load_messages("sess-atomic")
        assert [m["content"] for m in messages] == ["new question", "new answer"]
        assert messages[1]["metadata"] == {"k": 1}
        # The session row (and its CLI mapping) survives the replace — the old
        # save_history deleted the whole session row first.
        session = await store.get_session("sess-atomic")
        assert session["cli_session_id"] == "cli-1"
        assert session["model"] == "grok-4.3"

    @pytest.mark.asyncio
    async def test_replace_messages_rolls_back_atomically(self, store):
        """A mid-replace failure must leave the original history untouched."""
        await store.save_message("sess-rollback", "user", "original")

        bad_history = [
            {"role": "user", "content": "new"},
            # json.dumps raises TypeError on the unserializable metadata below
            {"role": "assistant", "content": "x", "metadata": {"bad": object()}},
        ]
        with pytest.raises(TypeError):
            await store.replace_messages("sess-rollback", bad_history)

        messages = await store.load_messages("sess-rollback")
        assert [m["content"] for m in messages] == ["original"]

    def test_pattern_cache_is_deleted(self):
        """pattern_cache was write-only dead weight (zero callers) — removed."""
        assert not hasattr(GrokSessionStore, "get_cached_pattern")
        assert not hasattr(GrokSessionStore, "save_cached_pattern")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 concurrency — read pool + passive connection recovery
# ─────────────────────────────────────────────────────────────────────────────

class TestReadPoolAndPassiveRecovery:
    """Reads run on a pooled read-only connection set and never serialize
    through the write lock; connection recovery is passive (retry-on-stale)
    instead of a proactive SELECT 1 ping on every operation."""

    @pytest.fixture
    async def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_chats_dir", classmethod(lambda cls: tmp_path))
        s = GrokSessionStore()
        yield s
        await s.close()

    def test_health_check_helpers_deleted(self):
        """The hot-path SELECT 1 ping is gone — recovery is passive-only."""
        assert not hasattr(GrokSessionStore, "_health_check")
        assert not hasattr(GrokSessionStore, "_health_check_unlocked")

    @pytest.mark.asyncio
    async def test_ensure_initialized_hot_path_issues_no_query(self, store):
        """After init, _ensure_initialized is a bare flag check — it must not
        touch the connection (the old version pinged SELECT 1 every call)."""
        await store._ensure_initialized()
        real_conn = store._conn
        probe = MagicMock()
        store._conn = probe
        try:
            await store._ensure_initialized()
        finally:
            store._conn = real_conn
        probe.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_reads_interleave_while_write_lock_held(self, store):
        """Two reads must complete while a write holds the write lock — reads
        previously serialized through the same lock and would deadlock here."""
        await store.save_session("sess-rw", model="grok-4.3")
        # Warm the pool so the reads below need no lazy setup.
        await store.get_session("sess-rw")

        release = asyncio.Event()

        async def blocked_write():
            async with store._lock:
                await release.wait()

        blocker = asyncio.create_task(blocked_write())
        await asyncio.sleep(0)  # let the blocker acquire the write lock
        assert store._lock.locked()

        try:
            session, sessions = await asyncio.wait_for(
                asyncio.gather(store.get_session("sess-rw"), store.list_sessions()),
                timeout=2.0,
            )
        finally:
            release.set()
            await blocker

        assert session["model"] == "grok-4.3"
        assert any(s["session_name"] == "sess-rw" for s in sessions)

    @pytest.mark.asyncio
    async def test_read_pool_round_robin_lazy_open(self, store, monkeypatch):
        """UNIGROK_READ_POOL_SIZE bounds the pool; connections open lazily and
        rotate round-robin across distinct aiosqlite connections."""
        monkeypatch.setenv("UNIGROK_READ_POOL_SIZE", "2")
        await store.save_session("sess-pool", model="grok-4.3")
        assert len(store._read_conns) == 0, "pool must be lazy — writes open nothing"

        for _ in range(3):
            await store.get_session("sess-pool")

        assert len(store._read_conns) == 2
        assert store._read_conns[0] is not store._read_conns[1]
        assert all(conn is not store._conn for conn in store._read_conns.values())

    @pytest.mark.asyncio
    async def test_read_connections_are_read_only(self, store):
        """Pooled connections carry PRAGMA query_only — a write through one
        must fail rather than bypass the write lock."""
        await store.save_session("sess-ro", model="grok-4.3")
        conn = await store._checkout_read_conn()
        with pytest.raises((sqlite3.OperationalError, aiosqlite.OperationalError)):
            await conn.execute("DELETE FROM sessions")

    @pytest.mark.asyncio
    async def test_stale_read_connection_recovers_passively(self, store):
        """A closed pooled connection triggers reset + retry inside the read
        wrapper — reads previously had no recovery at all."""
        await store.save_session("sess-stale", model="grok-4.3")
        assert (await store.get_session("sess-stale"))["model"] == "grok-4.3"

        for conn in list(store._read_conns.values()):
            await conn.close()

        result = await store.get_session("sess-stale")
        assert result["model"] == "grok-4.3"

    @pytest.mark.asyncio
    async def test_close_closes_read_pool(self, store):
        await store.save_session("sess-close", model="grok-4.3")
        await store.get_session("sess-close")
        assert len(store._read_conns) >= 1

        await store.close()
        assert store._read_conns == {}
        # And the store remains usable after close (lazy re-init + fresh pool).
        assert (await store.get_session("sess-close"))["model"] == "grok-4.3"


# ─────────────────────────────────────────────────────────────────────────────
# Now#6 (defensive) — xAI 2026 request surface + quick wins
# ─────────────────────────────────────────────────────────────────────────────

class TestXai2026Surface:
    """reasoning_effort profile plumbing and the conversation routing key are
    forwarded only when the installed SDK supports them."""

    def test_capability_gate_reads_installed_sdk(self):
        from src.utils import _chat_create_supports

        assert _chat_create_supports("model") is True
        assert _chat_create_supports("definitely_not_a_param_xyz") is False

    def test_profile_reasoning_effort_defaults_to_none(self):
        from src.utils import load_grok_profile

        profile = load_grok_profile("grok-4.3")
        assert profile["reasoning_effort"] is None

    def test_profile_reasoning_effort_normalized(self, tmp_path, monkeypatch):
        from src.utils import load_grok_profile

        monkeypatch.setattr(PathResolver, "get_service_root", staticmethod(lambda: tmp_path))
        hyper = tmp_path / ".grok" / "hyperparams"
        hyper.mkdir(parents=True)
        (hyper / "custom-effort.json").write_text(
            json.dumps({"temperature": 0.2, "top_p": 0.9, "reasoning_effort": "HIGH"}),
            encoding="utf-8",
        )
        (hyper / "bad-effort.json").write_text(
            json.dumps({"reasoning_effort": "turbo"}), encoding="utf-8"
        )

        assert load_grok_profile("custom-effort")["reasoning_effort"] == "high"
        assert load_grok_profile("bad-effort")["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_agentloop_forwards_reasoning_effort_and_conversation_id(self):
        from src.utils import _chat_create_supports

        if not (_chat_create_supports("reasoning_effort") and _chat_create_supports("conversation_id")):
            pytest.skip("installed xai_sdk does not expose the 2026 request surface")

        profile = {"profile": "p", "temperature": 0.3, "top_p": 0.9, "reasoning_effort": "high"}
        resp = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
            profile=profile,
        )

        with patch("xai_sdk.Client", return_value=mock_client):
            await loop.run("prompt", session="sess-conv")

        kwargs = mock_client.chat.create.call_args.kwargs
        assert kwargs["reasoning_effort"] == "high"
        assert kwargs["conversation_id"] == "sess-conv"

    @pytest.mark.asyncio
    async def test_agentloop_omits_surface_params_when_unset(self):
        """No session and no profile effort → the params never appear."""
        resp = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1),
            dynamic_sys_prompt="sys",
            model="grok-4.3",
        )

        with patch("xai_sdk.Client", return_value=mock_client):
            await loop.run("prompt")

        kwargs = mock_client.chat.create.call_args.kwargs
        assert "reasoning_effort" not in kwargs
        assert "conversation_id" not in kwargs

    @pytest.mark.asyncio
    async def test_discover_warns_when_default_models_missing_from_catalog(self, caplog):
        import logging as logging_module

        from src.utils import DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL, discover_xai_api_models

        fake_models = [SimpleNamespace(name="grok-9-preview", max_prompt_length=1000)]
        mock_client = MagicMock()
        mock_client.models.list_language_models.return_value = fake_models

        with patch("src.utils.get_xai_client", return_value=mock_client), \
             caplog.at_level(logging_module.WARNING, logger="GrokMCP"):
            result = await discover_xai_api_models()

        assert result["available"] is True
        messages = [record.getMessage() for record in caplog.records]
        assert any(DEFAULT_PLANNING_MODEL in msg and "absent" in msg for msg in messages)
        assert any(DEFAULT_CODING_MODEL in msg and "absent" in msg for msg in messages)


class TestUtilsQuickWins:
    """CLI fallback timeout env override."""

    @pytest.mark.asyncio
    async def test_cli_timeout_env_override(self, monkeypatch):
        """Quick win 14: the CLI fallback timeout is env-tunable (default 120s,
        was a hardcoded 10s that failed on any nontrivial prompt)."""
        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_CLI_TIMEOUT", "33")
        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        server_secrets = {
            "XAI_API_KEY": "xai-inference",
            "XAI_MANAGEMENT_API_KEY": "xai-management",
            "GROK_API_KEY": "grok-api",
            "OPENAI_API_KEY": "openai-api",
            "ANTHROPIC_API_KEY": "anthropic-api",
            "CLAUDE_API_KEY": "claude-api",
            "GEMINI_API_KEY": "gemini-api",
            "GOOGLE_API_KEY": "google-api",
            "GOOGLE_APPLICATION_CREDENTIALS": "/private/vertex-adc.json",
            "UNIGROK_API_KEYS": "gateway-client-secret",
        }
        for name, value in server_secrets.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("GROK_AUTH_PATH", "/oauth/auth.json")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "benign-project-id")

        class FakeProc:
            returncode = 0

        captured = {}

        async def fake_communicate(proc, timeout_sec, input_data=None):
            captured["timeout"] = timeout_sec
            return b"cli output", b""

        async def fake_exec(*cmd, **kwargs):
            captured["env"] = kwargs["env"]
            captured["start_new_session"] = kwargs["start_new_session"]
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        content, _, _, is_cli = await _call_plane("cli-fallback", "hi", None, None, "sys")

        assert is_cli is True
        assert content == "cli output"
        assert captured["timeout"] == 33.0
        assert captured["start_new_session"] is True
        assert server_secrets.keys().isdisjoint(captured["env"])
        assert captured["env"]["GROK_AUTH_PATH"] == "/oauth/auth.json"
        assert captured["env"]["GOOGLE_CLOUD_PROJECT"] == "benign-project-id"

    @pytest.mark.asyncio
    @pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
    async def test_owned_process_group_timeout_reaps_descendant(self, tmp_path):
        from src.utils import communicate_with_timeout

        child_pid_file = tmp_path / "child.pid"
        script = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import time; time.sleep(60)'], start_new_session=True); "
            "open(sys.argv[1],'w').write(str(child.pid)); "
            "time.sleep(60)"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            str(child_pid_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        proc._unigrok_process_group = True
        child_pid = None
        try:
            for _ in range(100):
                if child_pid_file.exists():
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("child PID was not published before timeout test")
            with pytest.raises(asyncio.TimeoutError):
                await communicate_with_timeout(proc, 0.3)
            assert proc.returncode is not None
            child_pid = int(child_pid_file.read_text())
            for _ in range(100):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("descendant survived owned process-group timeout")
        finally:
            if child_pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child_pid, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_cli_has_no_implicit_wall_clock_timeout(self, monkeypatch):
        """Slow native work must not fail at a hidden gateway deadline."""
        from src.utils import _call_plane

        monkeypatch.delenv("UNIGROK_CLI_TIMEOUT", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_communicate(proc, timeout_sec, input_data=None):
            captured["timeout"] = timeout_sec
            return b"complete", b""

        async def fake_exec(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        content, _, _, is_cli = await _call_plane(
            "cli-fallback", "finish it", None, None, "sys"
        )

        assert (content, is_cli) == ("complete", True)
        assert captured["timeout"] is None


class TestCliPlaneV2:
    """Dual-plane upgrade: JSON output parsing, deterministic -s session
    mapping (no more `grok sessions list` scraping), streaming-json deltas,
    and circuit-breaker integration on the CLI plane."""

    @staticmethod
    def _fake_store(session_row=None):
        store = MagicMock()
        store.get_session = AsyncMock(return_value=session_row)
        store.save_session = AsyncMock()
        store.load_messages = AsyncMock(return_value=[])
        return store

    @pytest.mark.asyncio
    async def test_api_plane_replays_history_from_injected_store(self):
        from src.utils import _call_plane

        injected_store = MagicMock(name="injected_store")
        expected_history = [
            {"role": "user", "content": "prior objective"},
            {"role": "assistant", "content": "prior receipt"},
        ]
        history_loader = AsyncMock(return_value=expected_history)
        response = _make_response(
            content="continued from injected history",
            tool_calls=[],
            cost_usd=0.003,
        )
        chat = MagicMock()
        chat.sample.return_value = response
        chat.append = MagicMock()
        client = MagicMock()
        client.chat.create.return_value = chat
        attempts = []

        def record_attempt(**attempt):
            attempts.append(attempt)

        with patch("src.utils.load_history", new=history_loader), patch(
            "src.utils.get_xai_client", return_value=client
        ):
            content, tokens, cost, is_cli = await _call_plane(
                "reasoning",
                "continue",
                "grok-owned-session",
                injected_store,
                "sys",
                requested_model="grok-build-0.1",
                attempt_recorder=record_attempt,
            )

        history_loader.assert_awaited_once_with(
            "grok-owned-session", injected_store
        )
        assert content == "continued from injected history"
        assert is_cli is False
        assert tokens > 0
        assert cost == pytest.approx(0.003)
        assert len(attempts) == chat.sample.call_count == 1

    @pytest.mark.asyncio
    async def test_cli_forwards_effort_max_turns_and_json_schema_exact_argv(self, monkeypatch):
        import json as json_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        monkeypatch.setattr(
            PathResolver, "get_grok_cli_path", staticmethod(lambda: "/tmp/grok")
        )
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return json_module.dumps({"text": "structured"}).encode(), b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        content, _, _, is_cli = await _call_plane(
            "cli-fallback",
            "hi",
            None,
            None,
            "sys",
            profile={"reasoning_effort": "high"},
            max_turns=5,
            json_schema=schema,
        )

        assert (content, is_cli) == ("structured", True)
        assert captured["cmd"] == [
            "/tmp/grok",
            "--system-prompt-override",
            "sys",
            "--effort",
            "high",
            "--max-turns",
            "5",
            "--json-schema",
            json_module.dumps(schema, sort_keys=True, separators=(",", ":")),
            "-p",
            "hi",
            "-m",
            "grok-composer-2.5-fast",
            "--output-format",
            "json",
        ]

    @pytest.mark.asyncio
    async def test_cli_streaming_session_exact_argv_includes_max_turns(self, monkeypatch):
        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        monkeypatch.setattr(
            PathResolver, "get_grok_cli_path", staticmethod(lambda: "/tmp/grok")
        )
        captured = {}

        class FakeStdout:
            def __init__(self):
                self._items = [
                    b'{"type":"text","data":"ok"}\n',
                    b'{"type":"end","sessionId":"sid-existing"}\n',
                ]

            async def readline(self):
                return self._items.pop(0) if self._items else b""

        class FakeStderr:
            async def read(self):
                return b""

        class FakeProc:
            returncode = 0

            def __init__(self):
                self.stdout = FakeStdout()
                self.stderr = FakeStderr()

            async def wait(self):
                return 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        events = []

        async def on_event(event):
            events.append(event)

        store = self._fake_store(session_row={"cli_session_id": "sid-existing"})
        content, _, _, is_cli = await _call_plane(
            "cli-fallback",
            "hi",
            "sess",
            store,
            "sys",
            on_event=on_event,
            max_turns=2,
        )

        assert (content, is_cli) == ("ok", True)
        assert captured["cmd"] == [
            "/tmp/grok",
            "--resume",
            "sid-existing",
            "--system-prompt-override",
            "sys",
            "--max-turns",
            "2",
            "-p",
            "hi",
            "-m",
            "grok-composer-2.5-fast",
            "--output-format",
            "streaming-json",
        ]
        store.save_session.assert_not_awaited()

    def test_cli_check_uses_oauth_only_models_probe_for_plane_health(self, monkeypatch, tmp_path):
        from src import utils

        monkeypatch.delenv("UNI_GROK_TESTING", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        server_secrets = {
            "XAI_API_KEY": "xai-inference",
            "XAI_MANAGEMENT_API_KEY": "xai-management",
            "GROK_API_KEY": "grok-api",
            "OPENAI_API_KEY": "openai-api",
            "ANTHROPIC_API_KEY": "anthropic-api",
            "CLAUDE_API_KEY": "claude-api",
            "GEMINI_API_KEY": "gemini-api",
            "GOOGLE_API_KEY": "google-api",
            "GOOGLE_APPLICATION_CREDENTIALS": "/private/vertex-adc.json",
            "UNIGROK_API_KEYS": "gateway-client-secret",
        }
        for name, value in server_secrets.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("GROK_AUTH_PATH", "/oauth/auth.json")
        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        (tmp_path / ".grok").mkdir()
        (tmp_path / ".grok" / "auth.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(utils, "grok_cli_available", lambda: True)
        monkeypatch.setattr(
            PathResolver, "get_grok_cli_path", staticmethod(lambda: "/tmp/grok")
        )
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                returncode=0,
                stdout=b"You are logged in with grok.com.\nAvailable models:\n- grok-4.5\n",
                stderr=b"",
            )

        monkeypatch.setattr(utils.subprocess, "run", fake_run)

        status = utils.grok_cli_plane_status(timeout_sec=3.0, force=True)
        assert status["ready"] is True
        assert status["models"] == ["grok-4.5"]
        assert captured["cmd"] == ["/tmp/grok", "models"]
        assert captured["kwargs"]["timeout"] == 3.0
        assert server_secrets.keys().isdisjoint(captured["kwargs"]["env"])
        assert captured["kwargs"]["env"]["GROK_AUTH_PATH"] == "/oauth/auth.json"
        assert captured["kwargs"]["env"]["UNIGROK_RUNTIME"] == "local"

    def test_cli_oauth_env_strips_all_server_credentials_only(self, monkeypatch):
        from src import utils

        server_secrets = {
            "XAI_API_KEY": "xai-inference",
            "XAI_MANAGEMENT_API_KEY": "xai-management",
            "GROK_API_KEY": "grok-api",
            "OPENAI_API_KEY": "openai-api",
            "ANTHROPIC_API_KEY": "anthropic-api",
            "CLAUDE_API_KEY": "claude-api",
            "GEMINI_API_KEY": "gemini-api",
            "GOOGLE_API_KEY": "google-api",
            "GOOGLE_APPLICATION_CREDENTIALS": "/private/vertex-adc.json",
            "UNIGROK_API_KEYS": "gateway-client-secret",
        }
        for name, value in server_secrets.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("GROK_AUTH_PATH", "/oauth/auth.json")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "benign-project-id")
        monkeypatch.setenv("UNIGROK_RUNTIME", "local")

        child_env = utils.grok_cli_oauth_env()

        assert server_secrets.keys().isdisjoint(child_env)
        assert child_env["GROK_AUTH_PATH"] == "/oauth/auth.json"
        assert child_env["GOOGLE_CLOUD_PROJECT"] == "benign-project-id"
        assert child_env["UNIGROK_RUNTIME"] == "local"

    def test_example_env_is_never_loaded_as_runtime_configuration(
        self, monkeypatch, tmp_path
    ):
        from src import utils

        (tmp_path / "example.env").write_text(
            "XAI_API_KEY=xai-example-must-not-load\n", encoding="utf-8"
        )
        loaded = []
        monkeypatch.setattr(utils, "load_dotenv", loaded.append)

        utils._load_service_environment(tmp_path)

        assert loaded == []

    def test_xai_placeholder_is_not_a_configured_credential(self, monkeypatch):
        from src import utils

        monkeypatch.setenv("XAI_API_KEY", "your_xai_api_key_here")

        assert utils._normalize_xai_api_key("your_xai_api_key_here") == ""
        assert utils.xai_api_key_configured() is False

    def test_isolated_cli_runtime_has_empty_workspace_and_durable_oauth_path(
        self, tmp_path, monkeypatch
    ):
        from src import utils

        source_home = tmp_path / "source-home"
        source_auth = source_home / ".grok" / "auth.json"
        source_auth.parent.mkdir(parents=True)
        source_auth.write_text(
            '{"account":{"access_token":"test-only","expires_at":1}}',
            encoding="utf-8",
        )
        (source_home / ".grok" / "settings.json").write_text(
            '{"mcpServers":{"unsafe":{}}}', encoding="utf-8"
        )
        monkeypatch.setenv("HOME", str(source_home))
        server_secrets = {
            "XAI_API_KEY": "xai-inference",
            "XAI_MANAGEMENT_API_KEY": "xai-management",
            "GROK_API_KEY": "grok-api",
            "OPENAI_API_KEY": "openai-api",
            "ANTHROPIC_API_KEY": "anthropic-api",
            "CLAUDE_API_KEY": "claude-api",
            "GEMINI_API_KEY": "gemini-api",
            "GOOGLE_API_KEY": "google-api",
            "GOOGLE_APPLICATION_CREDENTIALS": "/private/vertex-adc.json",
            "UNIGROK_API_KEYS": "gateway-client-secret",
        }
        for name, value in server_secrets.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

        with utils._isolated_grok_cli_runtime() as (cwd, env):
            isolated_root = cwd.parent
            assert list(cwd.iterdir()) == []
            assert env["GROK_AUTH_PATH"] == str(source_auth)
            assert list(Path(env["GROK_HOME"]).iterdir()) == []
            assert not (Path(env["HOME"]) / ".grok" / "auth.json").exists()
            assert not (Path(env["HOME"]) / ".grok" / "settings.json").exists()
            assert env["PWD"] == str(cwd)
            assert server_secrets.keys().isdisjoint(env)
            assert "UNRELATED_SECRET" not in env

        assert not isolated_root.exists()

    @pytest.mark.asyncio
    async def test_isolated_agent_turn_skips_inherited_dynamic_context(self, monkeypatch):
        from src import utils

        dynamic = AsyncMock(return_value=("must not be inherited", True, "ctx-leak"))
        routed = AsyncMock(return_value=utils.MetaLayer(generation="done"))
        monkeypatch.setattr(utils, "get_dynamic_context", dynamic)
        monkeypatch.setattr(utils, "orchestrate", routed)

        await utils.run_agent_turn(
            prompt="transform this",
            system_prompt="ONLY CALLER SYSTEM",
            enable_agentic=False,
            cli_isolated=True,
        )

        dynamic.assert_not_awaited()
        assert routed.await_args.kwargs["dynamic_sys_prompt"] == (
            "\nAdditional Instructions:\nONLY CALLER SYSTEM"
        )
        assert routed.await_args.kwargs["context_id"] is None
        assert routed.await_args.kwargs["cli_isolated"] is True

    def test_cli_plane_ready_uses_check_probe_outside_tests(self, monkeypatch):
        from src import utils

        monkeypatch.delenv("UNI_GROK_TESTING", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        monkeypatch.setattr(utils, "grok_cli_available", lambda: True)
        called = {"value": False}

        def fake_check_ready():
            called["value"] = True
            return True

        monkeypatch.setattr(utils, "grok_cli_check_ready", fake_check_ready)

        assert utils.cli_plane_ready_for_local_runtime() is True
        assert called["value"] is True

    @pytest.mark.asyncio
    async def test_cli_json_output_parsed_and_session_persisted(self, monkeypatch):
        import json as json_module
        import uuid as uuid_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        payload = json_module.dumps({
            "text": "hello from cli",
            "stopReason": "EndTurn",
            "sessionId": "11111111-2222-3333-4444-555555555555",
            "requestId": "req-1",
        }).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={})
        content, tokens, cost, is_cli = await _call_plane(
            "cli-fallback", "hi", "sess", store, "sys")

        assert content == "hello from cli"
        assert (tokens, cost, is_cli) == (0, 0.0, True)

        cmd = captured["cmd"]
        assert cmd[cmd.index("--output-format") + 1] == "json"
        # New sessions get a generated --session-id.
        generated = cmd[cmd.index("--session-id") + 1]
        uuid_module.UUID(generated)
        # ...but the CLI's reported sessionId wins for persistence.
        store.save_session.assert_awaited_once_with(
            "sess",
            cli_session_id="11111111-2222-3333-4444-555555555555",
            model="grok-composer-2.5-fast",
        )

    @pytest.mark.asyncio
    async def test_cli_resumes_stored_session_without_rewrite(self, monkeypatch):
        import json as json_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        payload = json_module.dumps({"text": "resumed", "sessionId": "abc-123"}).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "abc-123"})
        content, _, _, _ = await _call_plane("cli-fallback", "hi", "sess", store, "sys")

        assert content == "resumed"
        cmd = captured["cmd"]
        assert cmd[cmd.index("--resume") + 1] == "abc-123"
        assert "--session-id" not in cmd
        store.save_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_replays_server_history_when_stored_session_is_missing(
        self, monkeypatch
    ):
        import json as json_module
        import uuid as uuid_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {"cmds": []}

        class FakeProc:
            def __init__(self, returncode):
                self.returncode = returncode

        async def fake_exec(*cmd, **kwargs):
            captured["cmds"].append(list(cmd))
            return FakeProc(1 if len(captured["cmds"]) == 1 else 0)

        payload = json_module.dumps({
            "text": "recovered from server history",
            "sessionId": "fresh-cli-session",
        }).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            if proc.returncode:
                return (
                    b"",
                    b"Session abc-123 not found locally. Failed to restore session from remote: 404 Not Found",
                )
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "abc-123"})
        store.load_messages.return_value = [
            {"role": "user", "content": "Remember marker MISSING-SESSION-MARKER"},
            {"role": "assistant", "content": "Marker stored"},
        ]
        attempts = []

        def record_attempt(**attempt):
            attempts.append(attempt)

        content, _, _, is_cli = await _call_plane(
            "cli-fallback",
            "What marker did I give you?",
            "sess",
            store,
            "sys",
            attempt_recorder=record_attempt,
        )

        assert (content, is_cli) == ("recovered from server history", True)
        assert len(captured["cmds"]) == 2
        assert captured["cmds"][0][captured["cmds"][0].index("--resume") + 1] == "abc-123"
        fresh_cmd = captured["cmds"][1]
        assert "--resume" not in fresh_cmd
        assert "--fork-session" not in fresh_cmd
        uuid_module.UUID(fresh_cmd[fresh_cmd.index("--session-id") + 1])
        retry_prompt = fresh_cmd[fresh_cmd.index("-p") + 1]
        assert "MISSING-SESSION-MARKER" in retry_prompt
        assert retry_prompt.count("What marker did I give you?") == 1
        assert len(attempts) == len(captured["cmds"]) == 2
        assert [attempt["outcome"] for attempt in attempts] == [
            "error",
            "completed",
        ]
        store.load_messages.assert_awaited_once()
        store.save_session.assert_awaited_once_with(
            "sess",
            cli_session_id="fresh-cli-session",
            model="grok-composer-2.5-fast",
        )

    @pytest.mark.asyncio
    async def test_cli_forks_busy_session_with_fresh_mapping(self, monkeypatch):
        import json as json_module
        import uuid as uuid_module

        from src import utils
        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        monkeypatch.setattr(utils, "_BREAKER_STATE", {})
        captured = {"cmds": []}
        calls = {"count": 0}

        class FakeProc:
            def __init__(self, returncode):
                self.returncode = returncode

        async def fake_exec(*cmd, **kwargs):
            captured["cmds"].append(list(cmd))
            calls["count"] += 1
            return FakeProc(1 if calls["count"] == 1 else 0)

        payload = json_module.dumps({
            "text": "recovered",
            "sessionId": "forked-cli-session",
        }).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            if proc.returncode:
                return b"", b"Error: Session ID abc-123 is already in use."
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "abc-123"})
        content, _, _, is_cli = await _call_plane(
            "cli-fallback", "What marker did I give you?", "sess", store, "sys"
        )

        assert (content, is_cli) == ("recovered", True)
        assert len(captured["cmds"]) == 2
        assert captured["cmds"][0][captured["cmds"][0].index("--resume") + 1] == "abc-123"
        assert "--session-id" not in captured["cmds"][0]
        first_prompt = captured["cmds"][0][captured["cmds"][0].index("-p") + 1]
        assert first_prompt == "What marker did I give you?"
        fork_cmd = captured["cmds"][1]
        assert fork_cmd[fork_cmd.index("--resume") + 1] == "abc-123"
        assert "--fork-session" in fork_cmd
        fork_id = fork_cmd[fork_cmd.index("--session-id") + 1]
        uuid_module.UUID(fork_id)
        assert fork_cmd[fork_cmd.index("-p") + 1] == "What marker did I give you?"
        store.load_messages.assert_not_awaited()
        store.save_session.assert_awaited_once_with(
            "sess",
            cli_session_id="forked-cli-session",
            model="grok-composer-2.5-fast",
        )
        assert "grok-composer-2.5-fast" not in utils._BREAKER_STATE

    @pytest.mark.asyncio
    async def test_cli_replays_server_history_when_resume_and_fork_are_busy(
        self, monkeypatch
    ):
        import json as json_module
        import uuid as uuid_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {"cmds": []}

        class FakeProc:
            def __init__(self, returncode):
                self.returncode = returncode

        async def fake_exec(*cmd, **kwargs):
            captured["cmds"].append(list(cmd))
            return FakeProc(1 if len(captured["cmds"]) < 3 else 0)

        payload = json_module.dumps({
            "text": "recovered from server history",
            "sessionId": "fresh-cli-session",
        }).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            if proc.returncode:
                return b"", b"Error: Session ID abc-123 is already in use."
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "abc-123"})
        store.load_messages.return_value = [
            {"role": "user", "content": "Remember marker STALE-SESSION-MARKER"},
            {"role": "assistant", "content": "Marker stored"},
        ]
        content, _, _, is_cli = await _call_plane(
            "cli-fallback", "What marker did I give you?", "sess", store, "sys"
        )

        assert (content, is_cli) == ("recovered from server history", True)
        assert len(captured["cmds"]) == 3
        assert captured["cmds"][0][captured["cmds"][0].index("--resume") + 1] == "abc-123"
        assert "--fork-session" in captured["cmds"][1]
        fresh_cmd = captured["cmds"][2]
        assert "--resume" not in fresh_cmd
        assert "--fork-session" not in fresh_cmd
        uuid_module.UUID(fresh_cmd[fresh_cmd.index("--session-id") + 1])
        retry_prompt = fresh_cmd[fresh_cmd.index("-p") + 1]
        assert "# Server Conversation History" in retry_prompt
        assert "STALE-SESSION-MARKER" in retry_prompt
        assert "# Current User Request" in retry_prompt
        assert retry_prompt.count("What marker did I give you?") == 1
        store.load_messages.assert_awaited_once()
        store.save_session.assert_awaited_once_with(
            "sess",
            cli_session_id="fresh-cli-session",
            model="grok-composer-2.5-fast",
        )

    @pytest.mark.asyncio
    async def test_keyless_cli_uses_server_history_without_native_session_id(self, monkeypatch):
        import json as json_module

        from src.utils import _call_plane

        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "http")
        monkeypatch.setattr("src.utils.XAI_API_KEY", "")
        monkeypatch.setattr("src.utils.grok_cli_available", lambda: True)
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        payload = json_module.dumps({"text": "MCP-MARKER"}).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "stale-native-id"})
        store.load_messages.return_value = [
            {"role": "user", "content": "Remember this marker: MCP-MARKER"},
            {"role": "assistant", "content": "STORED"},
        ]

        content, _, _, is_cli = await _call_plane(
            "cli-fallback",
            "What marker did I give you?",
            "sess",
            store,
            "sys",
        )

        assert (content, is_cli) == ("MCP-MARKER", True)
        cmd = captured["cmd"]
        assert "--session-id" not in cmd
        assert "--resume" not in cmd
        prompt_arg = cmd[cmd.index("-p") + 1]
        assert "Server Conversation History" in prompt_arg
        assert "MCP-MARKER" in prompt_arg
        assert "Current User Request" in prompt_arg
        store.get_session.assert_not_awaited()
        store.save_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_explicit_messages_override_native_session_mapping(self, monkeypatch):
        import json as json_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return json_module.dumps({"text": "explicit history used"}).encode(), b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "native-cli-session"})
        content, _, _, is_cli = await _call_plane(
            "cli-fallback",
            "What marker did I give you?",
            "sess",
            store,
            "sys",
            input_messages=[
                {"role": "user", "content": "Remember marker EXPLICIT-MARKER"},
                {"role": "assistant", "content": "Marker stored"},
                {"role": "user", "content": "What marker did I give you?"},
            ],
        )

        assert (content, is_cli) == ("explicit history used", True)
        cmd = captured["cmd"]
        assert "--resume" not in cmd
        assert "--session-id" not in cmd
        prompt_arg = cmd[cmd.index("-p") + 1]
        assert "EXPLICIT-MARKER" in prompt_arg
        assert prompt_arg.count("What marker did I give you?") == 1
        store.get_session.assert_not_awaited()
        store.load_messages.assert_not_awaited()
        store.save_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_native_cli_failover_uses_session_id_not_prompt_stuffing(self, monkeypatch):
        import json as json_module

        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        monkeypatch.setattr(
            PathResolver, "get_grok_cli_path", staticmethod(lambda: "/tmp/grok")
        )
        captured = {}

        class FakeProc:
            returncode = 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        payload = json_module.dumps({
            "text": "context survived via native session",
            "sessionId": "native-cli-session",
        }).encode()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return payload, b""

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        store = self._fake_store(session_row={"cli_session_id": "native-cli-session"})
        store.load_messages.return_value = [
            {"role": "user", "content": "Remember marker API-MARKER"},
            {"role": "assistant", "content": "Stored server-side"},
        ]

        content, _, _, is_cli = await _call_plane(
            "cli-fallback",
            "What marker did I give you?",
            "sess",
            store,
            "sys",
        )

        assert (content, is_cli) == ("context survived via native session", True)
        cmd = captured["cmd"]
        assert cmd[cmd.index("--resume") + 1] == "native-cli-session"
        assert "--session-id" not in cmd
        prompt_arg = cmd[cmd.index("-p") + 1]
        assert prompt_arg == "What marker did I give you?"
        assert "Server Conversation History" not in prompt_arg
        assert "API-MARKER" not in prompt_arg
        store.load_messages.assert_not_awaited()
        store.save_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_plain_output_passthrough(self, monkeypatch):
        """Non-JSON stdout (older CLI builds) is returned verbatim."""
        from src.utils import _parse_cli_json_output

        assert _parse_cli_json_output("plain answer") == ("plain answer", None)
        assert _parse_cli_json_output('{"not-the-shape": 1}') == ('{"not-the-shape": 1}', None)
        assert _parse_cli_json_output('{"text": "hi", "sessionId": "s1"}') == ("hi", "s1")

    @pytest.mark.asyncio
    async def test_cli_streaming_json_forwards_deltas(self, monkeypatch):
        from src.utils import _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        captured = {}

        lines = [
            b'{"type":"text","data":"Hel"}\n',
            b'{"type":"thought","data":"thinking..."}\n',
            b'{"type":"text","data":"lo"}\n',
            b'{"type":"end","stopReason":"EndTurn","sessionId":"sid-9","requestId":"r1"}\n',
        ]

        class FakeStdout:
            def __init__(self, items):
                self._items = list(items)

            async def readline(self):
                return self._items.pop(0) if self._items else b""

        class FakeStderr:
            async def read(self):
                return b""

        class FakeProc:
            returncode = 0

            def __init__(self):
                self.stdout = FakeStdout(lines)
                self.stderr = FakeStderr()

            async def wait(self):
                return 0

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        events = []

        async def on_event(event):
            events.append(event)

        store = self._fake_store(session_row={})
        content, _, _, is_cli = await _call_plane(
            "cli-fallback", "hi", "sess", store, "sys", on_event=on_event)

        assert is_cli is True
        assert content == "Hello"
        deltas = [e["text"] for e in events if e.get("type") == "content_delta"]
        assert deltas == ["Hel", "lo"]
        cmd = captured["cmd"]
        assert cmd[cmd.index("--output-format") + 1] == "streaming-json"
        store.save_session.assert_awaited_once_with(
            "sess", cli_session_id="sid-9", model="grok-composer-2.5-fast")

    @pytest.mark.asyncio
    async def test_cli_failure_records_breaker(self, monkeypatch):
        from src import utils
        from src.utils import _GrokCLIExecutionError, _call_plane

        monkeypatch.setenv("UNIGROK_RUNTIME", "local")
        monkeypatch.setattr(utils, "_BREAKER_STATE", {})

        class FakeProc:
            returncode = 1

        async def fake_exec(*cmd, **kwargs):
            return FakeProc()

        async def fake_communicate(proc, timeout_sec, input_data=None):
            return (
                b'{"text":"receipt effect-7: edited file"}',
                b"boom with Bearer xai-123456789SECRET",
            )

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

        with pytest.raises(_GrokCLIExecutionError, match="Grok CLI error: boom") as exc_info:
            await _call_plane("cli-fallback", "hi", None, None, "sys")

        assert exc_info.value.partial_output == "receipt effect-7: edited file"
        assert "xai-123456789SECRET" not in str(exc_info.value)
        assert utils._BREAKER_STATE["grok-composer-2.5-fast"]["consecutive_failures"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Dynamic model resolution (catalog mocked, no network calls)
# ─────────────────────────────────────────────────────────────────────────────

class TestModelResolver:
    """Routing aliases (planning/coding/vision) resolve against the live
    catalog with a TTL cache; env overrides win over everything and hermetic
    tests never discover."""

    @staticmethod
    def _catalog(ids, available=True):
        return {
            "models": [{"id": mid} for mid in ids],
            "available": available,
            "warnings": [],
            "source": "test",
        }

    @pytest.mark.asyncio
    async def test_explicit_slug_passes_through(self):
        from src.utils import ModelResolver

        resolver = ModelResolver()
        with patch("src.utils.discover_xai_api_models", new_callable=AsyncMock) as mock_disc:
            assert await resolver.resolve("grok-custom-slug") == "grok-custom-slug"
        mock_disc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_testing_mode_short_circuits_to_constants(self):
        """UNI_GROK_TESTING=1 (set by conftest) must resolve to the static
        defaults without ever touching discovery."""
        from src.utils import DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL, ModelResolver

        resolver = ModelResolver()
        with patch("src.utils.discover_xai_api_models", new_callable=AsyncMock) as mock_disc:
            assert await resolver.resolve("planning") == DEFAULT_PLANNING_MODEL
            assert await resolver.resolve("coding") == DEFAULT_CODING_MODEL
            assert await resolver.resolve("vision") == DEFAULT_PLANNING_MODEL
        mock_disc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_env_override_wins_over_everything(self, monkeypatch):
        from src.utils import ModelResolver

        monkeypatch.setenv("UNIGROK_PLANNING_MODEL", "grok-pinned-plan")
        monkeypatch.setenv("UNIGROK_CODING_MODEL", "grok-pinned-code")
        resolver = ModelResolver()
        with patch("src.utils.discover_xai_api_models", new_callable=AsyncMock) as mock_disc:
            assert await resolver.resolve("planning") == "grok-pinned-plan"
            assert await resolver.resolve("coding") == "grok-pinned-code"
        mock_disc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_catalog_prefers_configured_default_when_present(self, monkeypatch):
        from src.utils import DEFAULT_PLANNING_MODEL, ModelResolver

        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        resolver = ModelResolver()
        catalog = self._catalog([DEFAULT_PLANNING_MODEL, "grok-9-reasoning"])
        with patch("src.utils.discover_xai_api_models", AsyncMock(return_value=catalog)):
            assert await resolver.resolve("planning") == DEFAULT_PLANNING_MODEL

    @pytest.mark.asyncio
    async def test_missing_default_picks_closest_with_warning(self, monkeypatch, caplog):
        """Retired default: planning picks the newest reasoning-capable slug,
        coding the newest code/build slug, with a WARNING naming old → new."""
        import logging as logging_module

        from src.utils import DEFAULT_PLANNING_MODEL, ModelResolver

        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        resolver = ModelResolver()
        catalog = self._catalog(["grok-9-reasoning", "grok-12", "grok-8-code-fast"])
        with patch("src.utils.discover_xai_api_models", AsyncMock(return_value=catalog)), \
             caplog.at_level(logging_module.WARNING, logger="GrokMCP"):
            planning = await resolver.resolve("planning")
            coding = await resolver.resolve("coding")

        assert planning == "grok-9-reasoning"
        assert coding == "grok-8-code-fast"
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert DEFAULT_PLANNING_MODEL in messages
        assert "grok-9-reasoning" in messages

    @pytest.mark.asyncio
    async def test_discovery_down_falls_back_to_constant(self, monkeypatch):
        from src.utils import DEFAULT_PLANNING_MODEL, ModelResolver

        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        resolver = ModelResolver()
        catalog = self._catalog(["grok-fallback"], available=False)
        with patch("src.utils.discover_xai_api_models", AsyncMock(return_value=catalog)):
            assert await resolver.resolve("planning") == DEFAULT_PLANNING_MODEL

    @pytest.mark.asyncio
    async def test_discovery_disabled_env_short_circuits(self, monkeypatch):
        from src.utils import DEFAULT_PLANNING_MODEL, ModelResolver

        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        monkeypatch.setenv("UNIGROK_MODEL_DISCOVERY", "0")
        resolver = ModelResolver()
        with patch("src.utils.discover_xai_api_models", new_callable=AsyncMock) as mock_disc:
            assert await resolver.resolve("planning") == DEFAULT_PLANNING_MODEL
        mock_disc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ttl_cache_discovers_once(self, monkeypatch):
        from src.utils import DEFAULT_PLANNING_MODEL, ModelResolver

        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        resolver = ModelResolver()
        mock_disc = AsyncMock(return_value=self._catalog([DEFAULT_PLANNING_MODEL]))
        with patch("src.utils.discover_xai_api_models", mock_disc):
            first = await resolver.resolve("planning")
            second = await resolver.resolve("planning")

        assert first == second == DEFAULT_PLANNING_MODEL
        assert mock_disc.await_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Server-side conversation state (SDK mocked, no network calls)
# ─────────────────────────────────────────────────────────────────────────────

class TestServerSideState:
    """Sessions ride xAI server-side state: store_messages=True on every turn,
    previous_response_id continues the stored thread on the next one, and the
    local history replay is skipped — SQLite stays the durable record."""

    _HISTORY = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]

    def _mock_client(self, resp):
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_chat.append = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        return mock_client, mock_chat

    def _mock_store(self, api_thread_id=None):
        mock_store = MagicMock()
        row = {"api_thread_id": api_thread_id} if api_thread_id is not None else None
        mock_store.get_session = AsyncMock(return_value=row)
        return mock_store

    def _skip_unless_supported(self):
        from src.utils import _server_state_supported

        if not _server_state_supported():
            pytest.skip("installed xai_sdk lacks store_messages/previous_response_id")

    @pytest.mark.asyncio
    async def test_first_turn_stores_messages_and_captures_response_id(self, monkeypatch):
        self._skip_unless_supported()
        monkeypatch.delenv("UNIGROK_SERVER_STATE", raising=False)
        resp = _make_response(content="answer", tool_calls=[], cost_usd=0.001)
        resp.id = "resp-first"
        mock_client, _ = self._mock_client(resp)
        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys",
            model="grok-4.3", store=self._mock_store(),
        )

        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await loop.run("hello", session="sess-state")

        kwargs = mock_client.chat.create.call_args.kwargs
        assert kwargs["store_messages"] is True
        assert "previous_response_id" not in kwargs
        assert layer.response_id == "resp-first"

    @pytest.mark.asyncio
    async def test_second_turn_uses_previous_response_id_and_skips_replay(self, monkeypatch):
        """With a saved response id, the turn continues the server thread and
        must NOT re-append the local history into the chat."""
        self._skip_unless_supported()
        monkeypatch.delenv("UNIGROK_SERVER_STATE", raising=False)
        resp = _make_response(content="answer 2", tool_calls=[], cost_usd=0.001)
        resp.id = "resp-second"
        mock_client, mock_chat = self._mock_client(resp)
        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys",
            model="grok-4.3", store=self._mock_store(api_thread_id="resp-first"),
        )

        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await loop.run("continue", session="sess-state", history=list(self._HISTORY))

        kwargs = mock_client.chat.create.call_args.kwargs
        assert kwargs["store_messages"] is True
        assert kwargs["previous_response_id"] == "resp-first"
        # system prompt + new user prompt + assistant response = 3 appends;
        # the two history messages were NOT replayed.
        assert mock_chat.append.call_count == 3
        assert layer.response_id == "resp-second"

    @pytest.mark.asyncio
    async def test_legacy_placeholder_id_never_sent_upstream(self, monkeypatch):
        """Old rows stored the session name in api_thread_id — that is not a
        response id and must trigger full local replay instead."""
        self._skip_unless_supported()
        monkeypatch.delenv("UNIGROK_SERVER_STATE", raising=False)
        resp = _make_response(content="answer", tool_calls=[], cost_usd=0.001)
        mock_client, mock_chat = self._mock_client(resp)
        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys",
            model="grok-4.3", store=self._mock_store(api_thread_id="sess-legacy"),
        )

        with patch("xai_sdk.Client", return_value=mock_client):
            await loop.run("continue", session="sess-legacy", history=list(self._HISTORY))

        kwargs = mock_client.chat.create.call_args.kwargs
        assert "previous_response_id" not in kwargs
        # system + 2 history messages + user prompt + response = 5 appends.
        assert mock_chat.append.call_count == 5

    @pytest.mark.asyncio
    async def test_kill_switch_restores_full_replay(self, monkeypatch):
        """UNIGROK_SERVER_STATE=0 disables the surface entirely: no
        store_messages, no previous_response_id, history replayed."""
        monkeypatch.setenv("UNIGROK_SERVER_STATE", "0")
        resp = _make_response(content="answer", tool_calls=[], cost_usd=0.001)
        mock_client, mock_chat = self._mock_client(resp)
        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys",
            model="grok-4.3", store=self._mock_store(api_thread_id="resp-first"),
        )

        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await loop.run("continue", session="sess-state", history=list(self._HISTORY))

        kwargs = mock_client.chat.create.call_args.kwargs
        assert "store_messages" not in kwargs
        assert "previous_response_id" not in kwargs
        assert mock_chat.append.call_count == 5
        assert layer.response_id == ""

    @pytest.mark.asyncio
    async def test_run_agent_turn_persists_response_id(self, monkeypatch):
        """The save path writes the turn's response id into
        sessions.api_thread_id and the message metadata."""
        monkeypatch.setattr(
            "src.utils.get_dynamic_context",
            AsyncMock(return_value=("system context", True, "ctx-1")),
        )
        monkeypatch.setattr(
            "src.utils.orchestrate",
            AsyncMock(return_value=MetaLayer(generation="done", response_id="resp-9")),
        )
        monkeypatch.setattr("src.utils.load_history", AsyncMock(return_value=[]))
        mock_append = AsyncMock()
        monkeypatch.setattr("src.utils.append_and_save_history", mock_append)
        mock_save = AsyncMock()
        monkeypatch.setattr("src.utils.store.save_session", mock_save)

        from src.utils import run_agent_turn
        await run_agent_turn(prompt="do work", session="s-state")

        assert mock_save.await_args.kwargs["api_thread_id"] == "resp-9"
        assert mock_append.await_args.kwargs["metadata"]["response_id"] == "resp-9"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Local history compaction (summarizer mocked, no network calls)
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryCompaction:
    """maybe_compact_history summarizes the oldest half into one system-role
    entry once the len/4 token estimate crosses the threshold. LOCAL
    compaction by design: the SDK's compact surface returns an opaque
    encrypted_content blob that cannot serve as the readable durable record."""

    @pytest.fixture
    async def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PathResolver, "get_chats_dir", classmethod(lambda cls: tmp_path))
        s = GrokSessionStore()
        yield s
        await s.close()

    @pytest.fixture(autouse=True)
    def _fold_latch_isolation(self):
        from src.utils import _reset_fold_latch

        _reset_fold_latch()
        yield
        _reset_fold_latch()

    @staticmethod
    def _history(n=8, pad=400):
        return [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " + "x" * pad}
            for i in range(n)
        ]

    def _mock_client(self, summary="COMPACT SUMMARY"):
        mock_chat = MagicMock()
        mock_chat.sample.return_value = SimpleNamespace(content=summary)
        # Explicit: this client has no working structured parse, so the fold
        # attempt degrades to the legacy prose path these tests exercise.
        mock_chat.parse.side_effect = RuntimeError("parse unavailable")
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        return mock_client

    def _mock_fold_client(self, fold, cost=0.0):
        """Client whose structured parse succeeds with the given fold."""
        mock_chat = MagicMock()
        mock_chat.parse.return_value = (SimpleNamespace(usage=None, cost_usd=cost), fold)
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        return mock_client, mock_chat

    @staticmethod
    def _fold(**overrides):
        from src.utils import FoldedSessionState

        defaults = dict(
            user_goal="ship the compaction feature",
            established_constraints=["must keep pytest green", "threshold stays env-driven"],
            failed_attempts=["regex-based session sync"],
            active_files=["src/utils.py"],
            narrative="midway through the fold rework",
        )
        defaults.update(overrides)
        return FoldedSessionState(**defaults)

    @pytest.mark.asyncio
    async def test_skipped_under_testing_without_force(self, monkeypatch, store):
        """UNI_GROK_TESTING=1 (conftest) must no-op unless force=True."""
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history()
        mock_get = MagicMock()
        with patch("src.utils.get_xai_client", mock_get):
            result = await maybe_compact_history("sess-c", history, store)

        assert result is history
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_threshold_is_a_no_op(self, store):
        from src.utils import maybe_compact_history

        history = self._history(n=4, pad=10)  # far below the 24000 default
        mock_get = MagicMock()
        with patch("src.utils.get_xai_client", mock_get):
            result = await maybe_compact_history("sess-c", history, store, force=True)

        assert result is history
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_compacts_oldest_half_into_system_entry(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)  # ~1600 estimated tokens
        with patch("src.utils.get_xai_client", return_value=self._mock_client()):
            result = await maybe_compact_history("sess-c", history, store, force=True)

        assert len(result) == 5  # 1 summary + newest 4
        assert result[0]["role"] == "system"
        assert "COMPACT SUMMARY" in result[0]["content"]
        assert [m["content"] for m in result[1:]] == [m["content"] for m in history[4:]]
        # The compacted history is persisted, not just returned.
        saved = await store.load_messages("sess-c")
        assert saved[0]["role"] == "system"
        assert "COMPACT SUMMARY" in saved[0]["content"]
        assert len(saved) == 5

    @pytest.mark.asyncio
    async def test_summarizer_failure_keeps_history(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        mock_client = MagicMock()
        mock_client.chat.create.return_value.sample.side_effect = ConnectionError("down")
        with patch("src.utils.get_xai_client", return_value=mock_client):
            result = await maybe_compact_history("sess-c", history, store, force=True)

        assert result is history
        assert await store.load_messages("sess-c") == []

    @pytest.mark.asyncio
    async def test_compaction_cost_lands_in_telemetry(self, monkeypatch, store):
        """The summarization is a real paid model call: its cost must be
        recorded in telemetry instead of dropped on the floor."""
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = SimpleNamespace(
            content="COMPACT SUMMARY", cost_usd=0.004
        )
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        with patch("src.utils.get_xai_client", return_value=mock_client):
            result = await maybe_compact_history("sess-cost", history, store, force=True)

        assert len(result) == 5
        rows = await store.get_telemetry_stats()
        compaction_rows = [r for r in rows if r["intent"] == "history-compaction"]
        assert len(compaction_rows) == 1
        assert compaction_rows[0]["cost"] == pytest.approx(0.004)
        assert compaction_rows[0]["success"] == 1

    @pytest.mark.asyncio
    async def test_compaction_telemetry_attributed_to_bound_caller(self, monkeypatch, store):
        """Regression (round-3 review): the compaction row rode save_telemetry
        without a caller — the ambient-contextvar fallback must attribute it
        so per-caller budgets count compaction spend too."""
        from src.utils import (
            maybe_compact_history,
            reset_active_caller,
            set_active_caller,
            telemetry_row_caller,
        )

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        token = set_active_caller("gemini-agent")
        try:
            with patch("src.utils.get_xai_client", return_value=self._mock_client()):
                await maybe_compact_history("sess-attr", history, store, force=True)
        finally:
            reset_active_caller(token)

        rows = await store.get_telemetry_stats()
        compaction_rows = [r for r in rows if r["intent"] == "history-compaction"]
        assert len(compaction_rows) == 1
        assert telemetry_row_caller(compaction_rows[0]) == "gemini-agent"

    @pytest.mark.asyncio
    async def test_open_breaker_skips_compaction_gracefully(self, monkeypatch, store):
        """The summarizer rides the per-model circuit breaker like every other
        upstream call: an open breaker skips compaction without a model call,
        and a summarizer failure counts against the breaker."""
        from src.utils import maybe_compact_history, record_xai_failure, resolve_model

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        monkeypatch.setenv("UNIGROK_BREAKER_THRESHOLD", "1")
        history = self._history(n=8, pad=800)
        record_xai_failure(await resolve_model("coding"))  # trips at threshold 1

        mock_get = MagicMock()
        with patch("src.utils.get_xai_client", mock_get):
            result = await maybe_compact_history("sess-brk", history, store, force=True)

        assert result is history
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_agentloop_replays_system_history_entries(self, monkeypatch):
        """Compaction summaries are system-role entries — the replay path must
        forward them (previously only user/assistant were replayed)."""
        monkeypatch.setenv("UNIGROK_SERVER_STATE", "0")
        history = [
            {"role": "system", "content": "[Compacted summary] earlier facts"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        resp = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys", model="grok-4.3",
        )
        with patch("xai_sdk.Client", return_value=mock_client):
            await loop.run("prompt", session="sess-sys", history=history)

        # sys prompt + summary + q + a + new prompt + response = 6 appends.
        assert mock_chat.append.call_count == 6

    # ── Structured state folding ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_folds_structured_state_into_system_entry(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        client, _chat = self._mock_fold_client(self._fold())
        with patch("src.utils.get_xai_client", return_value=client):
            result = await maybe_compact_history("sess-fold", history, store, force=True)

        assert len(result) == 5  # 1 fold + newest 4
        entry = result[0]
        assert entry["role"] == "system"
        assert entry["content"].startswith("[Compacted state fold of 4 earlier messages")
        assert "GOAL: ship the compaction feature" in entry["content"]
        assert "CONSTRAINTS:\n- must keep pytest green" in entry["content"]
        assert "DEAD ENDS (do not retry):\n- regex-based session sync" in entry["content"]
        assert "ACTIVE FILES:\n- src/utils.py" in entry["content"]
        assert "NARRATIVE: midway through the fold rework" in entry["content"]
        assert [m["content"] for m in result[1:]] == [m["content"] for m in history[4:]]
        saved = await store.load_messages("sess-fold")
        assert saved[0]["content"] == entry["content"]

    @pytest.mark.asyncio
    async def test_fold_failure_falls_back_to_prose_same_call(self, monkeypatch, store):
        """A failed fold degrades to the prose summary in the SAME call (the
        threshold already fired) and never ticks the circuit breaker."""
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        failure_spy = MagicMock()
        monkeypatch.setattr("src.utils.record_xai_failure", failure_spy)
        with patch("src.utils.get_xai_client", return_value=self._mock_client()):
            result = await maybe_compact_history("sess-fb", history, store, force=True)

        assert len(result) == 5
        assert result[0]["content"].startswith("[Compacted summary of 4 earlier messages")
        assert "COMPACT SUMMARY" in result[0]["content"]
        failure_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_fold_and_prose_failure_keeps_history(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        failure_spy = MagicMock()
        monkeypatch.setattr("src.utils.record_xai_failure", failure_spy)
        mock_chat = MagicMock()
        mock_chat.parse.side_effect = RuntimeError("parse down")
        mock_chat.sample.side_effect = ConnectionError("down")
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        with patch("src.utils.get_xai_client", return_value=mock_client):
            result = await maybe_compact_history("sess-both", history, store, force=True)

        assert result is history
        assert await store.load_messages("sess-both") == []
        failure_spy.assert_called_once()  # prose only — never the fold

    @pytest.mark.asyncio
    async def test_fold_disabled_env_forces_prose(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        monkeypatch.setenv("UNIGROK_COMPACT_FOLD", "0")
        history = self._history(n=8, pad=800)
        client, chat = self._mock_fold_client(self._fold())
        chat.sample.return_value = SimpleNamespace(content="COMPACT SUMMARY")
        with patch("src.utils.get_xai_client", return_value=client):
            result = await maybe_compact_history("sess-off", history, store, force=True)

        chat.parse.assert_not_called()
        assert "COMPACT SUMMARY" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_budget_relative_threshold_triggers_below_flat(self, monkeypatch, store):
        """min(flat, ratio × model context): a small-context model_hint lowers
        the effective threshold; the same history without a hint is a no-op."""
        from src.utils import maybe_compact_history

        monkeypatch.delenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", raising=False)  # flat 24000
        monkeypatch.setenv("UNIGROK_COMPACT_CONTEXT_RATIO", "0.5")
        monkeypatch.setattr("src.utils.get_model_max_tokens", MagicMock(return_value=8000))
        history = self._history(n=8, pad=2500)  # ~5000 estimated tokens
        client, _chat = self._mock_fold_client(self._fold())

        with patch("src.utils.get_xai_client", return_value=client):
            no_hint = await maybe_compact_history("sess-b1", history, store, force=True)
        assert no_hint is history  # 5000 < flat 24000, no hint → no clamp

        with patch("src.utils.get_xai_client", return_value=client):
            hinted = await maybe_compact_history(
                "sess-b2", history, store, force=True, model_hint="grok-tiny"
            )
        assert len(hinted) == 5  # 5000 >= min(24000, 0.5*8000) = 4000

    @pytest.mark.asyncio
    async def test_context_ratio_zero_disables_budget_clamp(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.delenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", raising=False)
        monkeypatch.setenv("UNIGROK_COMPACT_CONTEXT_RATIO", "0")
        monkeypatch.setattr("src.utils.get_model_max_tokens", MagicMock(return_value=8000))
        history = self._history(n=8, pad=2500)
        mock_get = MagicMock()
        with patch("src.utils.get_xai_client", mock_get):
            result = await maybe_compact_history(
                "sess-r0", history, store, force=True, model_hint="grok-tiny"
            )

        assert result is history
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_refold_previous_fold_stays_bounded(self, monkeypatch, store):
        """A prior fold entry lands in the oldest half next compaction; the
        result must contain exactly one bounded fold entry, never a stack."""
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        history[0] = {
            "role": "system",
            "content": "[Compacted state fold of 6 earlier messages in this session]\n"
                       "GOAL: earlier goal\nNARRATIVE: " + "n" * 3000,
        }
        big_fold = self._fold(narrative="m" * 5000)  # render caps at 1200/3500
        client, _chat = self._mock_fold_client(big_fold)
        with patch("src.utils.get_xai_client", return_value=client):
            result = await maybe_compact_history("sess-refold", history, store, force=True)

        fold_entries = [m for m in result if "[Compacted state fold" in str(m.get("content"))]
        assert len(fold_entries) == 1
        assert len(fold_entries[0]["content"]) <= 3500

    @pytest.mark.asyncio
    async def test_fold_render_redacts_secrets(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        leaky = self._fold(established_constraints=["use key xai-abcdefgh12345678 for calls"])
        client, _chat = self._mock_fold_client(leaky)
        with patch("src.utils.get_xai_client", return_value=client):
            result = await maybe_compact_history("sess-red", history, store, force=True)

        assert "xai-abcdefgh12345678" not in result[0]["content"]
        assert "[REDACTED_KEY]" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_fold_telemetry_records_folded_flag_and_model(self, monkeypatch, store):
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        client, _chat = self._mock_fold_client(self._fold(), cost=0.002)
        with patch("src.utils.get_xai_client", return_value=client):
            await maybe_compact_history("sess-tel1", history, store, force=True)

        rows = await store.get_telemetry_stats()
        row = [r for r in rows if r["intent"] == "history-compaction"][0]
        meta = json.loads(row["metadata"])
        assert meta["folded"] is True
        assert meta["model"]
        assert row["cost"] == pytest.approx(0.002)

    @pytest.mark.asyncio
    async def test_prose_fallback_telemetry_sums_both_calls(self, monkeypatch, store):
        """When the fold paid but failed and prose succeeded, the telemetry
        row records folded=false and the summed spend of both calls."""
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        mock_chat = MagicMock()
        # Fold parses but validation-fails inside _parse_structured → the
        # seam reports (None, 0, 0.0); prose then succeeds with its own cost.
        mock_chat.parse.side_effect = RuntimeError("validation failed")
        mock_chat.sample.return_value = SimpleNamespace(content="COMPACT SUMMARY", cost_usd=0.004)
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat
        with patch("src.utils.get_xai_client", return_value=mock_client):
            await maybe_compact_history("sess-tel2", history, store, force=True)

        rows = await store.get_telemetry_stats()
        row = [r for r in rows if r["intent"] == "history-compaction"][0]
        assert json.loads(row["metadata"])["folded"] is False
        assert row["cost"] == pytest.approx(0.004)

    @pytest.mark.asyncio
    async def test_fold_latch_disables_after_consecutive_failures(self, monkeypatch, store):
        """A fold failure means paying for two calls (fold + prose). After
        consecutive failures — persistent parse unavailability, not a blip —
        folds self-disable for the process so compaction stops paying twice."""
        from src.utils import maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        with patch("src.utils.get_xai_client", return_value=self._mock_client()):
            await maybe_compact_history("sess-l1", history, store, force=True)
            await maybe_compact_history("sess-l2", history, store, force=True)

        # Third compaction: even a fold-capable client is never asked to parse.
        client, chat = self._mock_fold_client(self._fold())
        chat.sample.return_value = SimpleNamespace(content="COMPACT SUMMARY")
        with patch("src.utils.get_xai_client", return_value=client):
            result = await maybe_compact_history("sess-l3", history, store, force=True)

        chat.parse.assert_not_called()
        assert "COMPACT SUMMARY" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_fold_latch_resets_on_success(self, monkeypatch, store):
        from src.utils import _COMPACT_FOLD_STATE, maybe_compact_history

        monkeypatch.setenv("UNIGROK_COMPACT_THRESHOLD_TOKENS", "1000")
        history = self._history(n=8, pad=800)
        with patch("src.utils.get_xai_client", return_value=self._mock_client()):
            await maybe_compact_history("sess-r1", history, store, force=True)
        assert _COMPACT_FOLD_STATE["consecutive_failures"] == 1

        client, _chat = self._mock_fold_client(self._fold())
        with patch("src.utils.get_xai_client", return_value=client):
            await maybe_compact_history("sess-r2", history, store, force=True)

        assert _COMPACT_FOLD_STATE["consecutive_failures"] == 0
        assert _COMPACT_FOLD_STATE["disabled"] is False

    @pytest.mark.asyncio
    async def test_agentloop_replays_folded_state_entry(self, monkeypatch):
        """Folded state entries are system-role like prose summaries — the
        replay path must forward their full content to the model."""
        monkeypatch.setenv("UNIGROK_SERVER_STATE", "0")
        seen_system = []

        def _capture_system(content):
            seen_system.append(content)
            return ("system", content)

        monkeypatch.setattr("xai_sdk.chat.system", _capture_system)
        fold_block = (
            "[Compacted state fold of 4 earlier messages in this session]\n"
            "GOAL: keep the suite green\n"
            "DEAD ENDS (do not retry):\n- reverting the schema"
        )
        history = [
            {"role": "system", "content": fold_block},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        resp = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys", model="grok-4.3",
        )
        with patch("xai_sdk.Client", return_value=mock_client):
            await loop.run("prompt", session="sess-fold-replay", history=history)

        assert mock_chat.append.call_count == 6
        assert fold_block in seen_system


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Agent progress events and real streaming (SDK mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentProgressEvents:
    """on_event progress surface: depth + tool events from AgentLoop, real
    content deltas from the fast plane's chat.stream() bridge."""

    @pytest.mark.asyncio
    async def test_agentloop_emits_depth_and_tool_events(self):
        events = []

        async def on_event(event):
            events.append(event)

        async def tool_fn(**kwargs):
            return "tool output"

        register_internal_tool("__evt_tool__", tool_fn)
        tc = MagicMock()
        tc.function.name = "__evt_tool__"
        tc.function.arguments = "{}"
        tc.id = "call-evt-1"
        resp1 = _make_response(content="using tool", tool_calls=[tc], cost_usd=0.001)
        resp2 = _make_response(content="final", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.side_effect = [resp1, resp2]
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=3), dynamic_sys_prompt="sys",
            model="grok-4.3", on_event=on_event,
        )
        try:
            with patch("xai_sdk.Client", return_value=mock_client):
                layer = await loop.run("prompt")
        finally:
            _INTERNAL_TOOL_REGISTRY.pop("__evt_tool__", None)

        kinds = [event["type"] for event in events]
        assert kinds.count("depth") == 2
        assert "tool_start" in kinds and "tool_end" in kinds
        tool_end = next(event for event in events if event["type"] == "tool_end")
        assert tool_end["tool"] == "__evt_tool__"
        assert tool_end["success"] is True
        assert all("cost_usd" in event for event in events)
        assert layer.generation == "final"

    @pytest.mark.asyncio
    async def test_call_plane_streams_deltas_via_on_event(self):
        """With on_event set, the API branch uses chat.stream() and forwards
        each chunk as a content_delta event; sample() is never called."""
        from src.utils import _call_plane

        events = []

        def on_event(event):  # sync callback: both flavors are supported
            events.append(event)

        final = _make_response(content="Hello world", tool_calls=[], cost_usd=0.002)
        mock_chat = MagicMock()
        mock_chat.stream.return_value = iter([
            (final, SimpleNamespace(content="Hello ")),
            (final, SimpleNamespace(content="world")),
        ])
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        with patch("xai_sdk.Client", return_value=mock_client):
            content, _, _, is_cli = await _call_plane(
                "reasoning", "hi", None, None, "sys",
                requested_model="grok-4.3", on_event=on_event,
            )
        await asyncio.sleep(0)  # drain any last threadsafe event dispatch

        assert content == "Hello world"
        assert is_cli is False
        mock_chat.sample.assert_not_called()
        deltas = [event["text"] for event in events if event["type"] == "content_delta"]
        assert deltas == ["Hello ", "world"]

    @pytest.mark.asyncio
    async def test_event_callback_failure_never_breaks_run(self):
        def bad_event(event):
            raise RuntimeError("observer exploded")

        resp = _make_response(content="fine", tool_calls=[], cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=1), dynamic_sys_prompt="sys",
            model="grok-4.3", on_event=bad_event,
        )
        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await loop.run("prompt")

        assert layer.generation == "fine"
        assert layer.finish_reason == "final_answer"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Self-escalation (escalate_reasoning) — SDK mocked, no network
# ─────────────────────────────────────────────────────────────────────────────

# Escalation chat/client doubles moved to evals/fakes.py (FakeChat/FakeClient)
# so the offline eval harness replays the same escalation path these tests
# pin. FakeChat records appends; FakeClient(chats=[...]) hands out the queued
# chats while recording every create() kwargs.
_FakeEscalationChat = FakeChat


def _FakeEscalationClient(chats):
    return FakeClient(chats=chats)


def _escalation_tc(call_id="call-esc-1", reason="needs deeper reasoning"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = "escalate_reasoning"
    tc.function.arguments = json.dumps({"reason": reason})
    return tc


class TestSelfEscalation:
    """escalate_reasoning: one-way, once-per-run hand-off from the coding
    model to the planning model, rebuilt mid-loop with the full conversation."""

    @pytest.mark.asyncio
    async def test_escalation_rebuilds_on_planning_model_and_continues(self):
        """A dispatched escalation must rebuild the chat on the planning model
        carrying every existing message, then keep sampling to a final answer."""
        from src.utils import DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL

        resp1 = _make_response(content="hmm", tool_calls=[_escalation_tc()], cost_usd=0.001)
        resp2 = _make_response(content="deep answer", tool_calls=[], cost_usd=0.001)
        chat1 = _FakeEscalationChat([resp1])
        chat2 = _FakeEscalationChat([resp2])
        client = _FakeEscalationClient([chat1, chat2])

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=3), dynamic_sys_prompt="sys",
            model=DEFAULT_CODING_MODEL,
        )
        with patch("src.utils.get_xai_client", return_value=client):
            layer = await loop.run("hard prompt")

        assert len(client.create_calls) == 2
        assert client.create_calls[0]["model"] == DEFAULT_CODING_MODEL
        assert client.create_calls[1]["model"] == DEFAULT_PLANNING_MODEL
        # The escalation tool schema was offered on the coding-model start.
        assert any("escalate_reasoning" in str(t) for t in client.create_calls[0]["tools"])
        # The rebuilt chat carries the FULL prior conversation verbatim.
        assert chat2.messages[: len(chat1.messages)] == chat1.messages
        assert layer.escalated is True
        assert layer.generation == "deep answer"
        assert layer.finish_reason == "final_answer"
        accepted = [t for t in layer.tool_trace if t["tool_name"] == "escalate_reasoning"]
        assert accepted and accepted[0]["content"] == (
            f"escalation accepted — continuing with {DEFAULT_PLANNING_MODEL}"
        )

    @pytest.mark.asyncio
    async def test_second_escalation_call_is_noop(self):
        """Escalation is once per run: a second call gets a no-op observation
        and no third chat is ever created."""
        from src.utils import DEFAULT_CODING_MODEL

        resp1 = _make_response(content="", tool_calls=[_escalation_tc("call-1")], cost_usd=0.001)
        resp2 = _make_response(content="", tool_calls=[_escalation_tc("call-2")], cost_usd=0.001)
        resp3 = _make_response(content="final", tool_calls=[], cost_usd=0.001)
        chat1 = _FakeEscalationChat([resp1])
        chat2 = _FakeEscalationChat([resp2, resp3])
        client = _FakeEscalationClient([chat1, chat2])

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=4), dynamic_sys_prompt="sys",
            model=DEFAULT_CODING_MODEL,
        )
        with patch("src.utils.get_xai_client", return_value=client):
            layer = await loop.run("hard prompt")

        assert len(client.create_calls) == 2  # exactly one rebuild, ever
        contents = [t["content"] for t in layer.tool_trace if t["tool_name"] == "escalate_reasoning"]
        assert any(c.startswith("escalation accepted") for c in contents)
        assert "escalation already active — one escalation per run." in contents
        assert layer.escalated is True
        assert layer.generation == "final"

    @pytest.mark.asyncio
    async def test_escalation_unavailable_when_started_on_planning_model(self):
        """A loop that starts on the planning model never offers the tool and
        rejects a stray call without rebuilding."""
        from src.utils import DEFAULT_PLANNING_MODEL

        resp1 = _make_response(content="", tool_calls=[_escalation_tc()], cost_usd=0.001)
        resp2 = _make_response(content="done", tool_calls=[], cost_usd=0.001)
        chat1 = _FakeEscalationChat([resp1, resp2])
        client = _FakeEscalationClient([chat1])

        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=3), dynamic_sys_prompt="sys",
            model=DEFAULT_PLANNING_MODEL,
        )
        with patch("src.utils.get_xai_client", return_value=client):
            layer = await loop.run("prompt")

        assert len(client.create_calls) == 1
        assert not any(
            "escalate_reasoning" in str(t) for t in client.create_calls[0]["tools"]
        )
        assert layer.escalated is False
        rejected = [t for t in layer.tool_trace if t["tool_name"] == "escalate_reasoning"]
        assert rejected and rejected[0]["success"] is False
        assert "unavailable" in rejected[0]["content"]

    @pytest.mark.asyncio
    async def test_rebuild_failure_degrades_to_current_model(self):
        """If the planning-model rebuild raises, the run continues on the
        coding chat and never reports escalated=True."""
        from src.utils import DEFAULT_CODING_MODEL

        resp1 = _make_response(content="", tool_calls=[_escalation_tc()], cost_usd=0.001)
        resp2 = _make_response(content="recovered", tool_calls=[], cost_usd=0.001)
        chat1 = _FakeEscalationChat([resp1, resp2])

        calls = []

        def _create(**kwargs):
            calls.append(kwargs)
            if len(calls) > 1:
                raise RuntimeError("planning model unavailable")
            return chat1

        client = SimpleNamespace(chat=SimpleNamespace(create=_create))
        loop = AgentLoop(
            policy=AgentLoopPolicy(max_depth=3), dynamic_sys_prompt="sys",
            model=DEFAULT_CODING_MODEL,
        )
        with patch("src.utils.get_xai_client", return_value=client):
            layer = await loop.run("prompt")

        assert len(calls) == 2  # rebuild attempted exactly once
        assert layer.escalated is False
        assert layer.generation == "recovered"
        assert layer.finish_reason == "final_answer"
        assert loop.model == DEFAULT_CODING_MODEL  # restored after the failure

    @pytest.mark.asyncio
    async def test_save_task_memory_safe_records_escalation_metadata(self):
        """Escalated runs persist {'escalated': True} into task-memory
        metadata; non-escalated runs persist no metadata at all."""
        from src.utils import _save_task_memory_safe

        mock_store = MagicMock()
        mock_store.save_task_memory = AsyncMock()

        await _save_task_memory_safe(
            mock_store, "p", MetaLayer(generation="done", escalated=True), "m", 1
        )
        assert mock_store.save_task_memory.await_args.kwargs["metadata"] == {"escalated": True}

        await _save_task_memory_safe(
            mock_store, "p", MetaLayer(generation="done"), "m", 1
        )
        assert mock_store.save_task_memory.await_args.kwargs["metadata"] is None

        await _save_task_memory_safe(
            mock_store, "p", MetaLayer(generation="done"), "m", None
        )
        assert mock_store.save_task_memory.await_args.kwargs["metadata"] == {
            "outcome_verified": False
        }

    @pytest.mark.asyncio
    async def test_run_agent_turn_records_escalation_in_message_metadata(self, monkeypatch):
        """The gateway save path stamps escalated=True into the persisted
        message metadata alongside response_id/tool_trace."""
        monkeypatch.setattr(
            "src.utils.get_dynamic_context",
            AsyncMock(return_value=("system context", True, "ctx-esc")),
        )
        monkeypatch.setattr(
            "src.utils.orchestrate",
            AsyncMock(return_value=MetaLayer(generation="done", escalated=True)),
        )
        monkeypatch.setattr("src.utils.load_history", AsyncMock(return_value=[]))
        mock_append = AsyncMock()
        monkeypatch.setattr("src.utils.append_and_save_history", mock_append)
        monkeypatch.setattr("src.utils.store.save_session", AsyncMock())

        from src.utils import run_agent_turn
        await run_agent_turn(prompt="do work", session="s-esc")

        assert mock_append.await_args.kwargs["metadata"]["escalated"] is True

    @pytest.mark.asyncio
    async def test_task_memory_metadata_roundtrip_and_notes(self, tmp_path):
        """metadata survives the SQLite roundtrip as a dict and surfaces in
        the formatted memory notes so future turns see the escalation."""
        from src.utils import format_task_memory_notes

        s = GrokSessionStore(db_path=tmp_path / "esc_memory.db")
        try:
            await s.save_task_memory(
                prompt="refactor the routing layer",
                outcome_summary="needed planning model",
                plane="API", model="grok-4.3", profile="default",
                success=1, latency=1.0, cost=0.01,
                metadata={"escalated": True},
            )
            memories = await s.get_similar_task_memories("refactor the routing layer")
            assert memories and memories[0]["metadata"] == {"escalated": True}
            notes = format_task_memory_notes(memories)
            assert "needed escalation to the planning model" in notes

            await s.save_task_memory(
                prompt="refactor the unknown layer",
                outcome_summary="provider returned text",
                plane="API", model="grok-4.3", profile="default",
                success=None, latency=1.0, cost=0.01,
            )
            unknown = await s.get_similar_task_memories("refactor the unknown layer")
            assert "unverified" in format_task_memory_notes(unknown)
        finally:
            await s.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid", (-1, 2, 0.0, 1.0, "1"))
    async def test_outcome_storage_rejects_values_outside_tristate(
        self, tmp_path, invalid
    ):
        s = GrokSessionStore(db_path=tmp_path / f"invalid-{invalid!s}.db")
        try:
            with pytest.raises(ValueError, match="success must be"):
                await s.save_telemetry("intent", "API", invalid, 1.0, 0.0)
            with pytest.raises(ValueError, match="success must be"):
                await s.save_task_memory(
                    prompt="task", outcome_summary="outcome", plane="API",
                    model="grok-4.5", profile="default", success=invalid,
                    latency=1.0, cost=0.0,
                )
            assert await s.get_telemetry_stats() == []
            assert await s.get_task_memory_count() == 0
        finally:
            await s.close()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — RoutingAdvisor (telemetry-informed borderline prior)
# ─────────────────────────────────────────────────────────────────────────────

def _advisor_stats(planning_model, coding_model, p_samples=40, p_rate=0.9,
                   c_samples=40, c_rate=0.5):
    return [
        {"plane": "API", "model": planning_model, "samples": p_samples,
         "success_rate": p_rate, "avg_cost": 0.01},
        {"plane": "API", "model": coding_model, "samples": c_samples,
         "success_rate": c_rate, "avg_cost": 0.002},
    ]


class TestRoutingAdvisor:
    """Borderline routing scores (exactly 1) statically fall to the coding
    model; the advisor flips them to planning only on strong recent telemetry
    (success-rate margin + minimum samples for BOTH models)."""

    def _fake_agent_loop(self, monkeypatch):
        captured = {}

        class _FakeLoop:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def run(self, *args, **kwargs):
                return MetaLayer(generation="ok", finish_reason="final_answer")

        monkeypatch.setattr("src.utils.AgentLoop", _FakeLoop)
        return captured

    @pytest.mark.asyncio
    async def test_borderline_prompt_flips_to_planning_with_favorable_stats(self, monkeypatch):
        from src.utils import (
            DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL,
            get_routing_advisor, orchestrate,
        )

        get_routing_advisor().inject_stats(
            _advisor_stats(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL)
        )
        captured = self._fake_agent_loop(monkeypatch)
        monkeypatch.setattr("src.utils.routing_reason_score", lambda prompt: 1)

        layer = await orchestrate("borderline prompt", mode="auto")

        assert layer.generation == "ok"
        assert captured["model"] == DEFAULT_PLANNING_MODEL

    @pytest.mark.asyncio
    async def test_insufficient_samples_keep_static_prior(self, monkeypatch):
        from src.utils import (
            DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL,
            get_routing_advisor, orchestrate,
        )

        get_routing_advisor().inject_stats(
            _advisor_stats(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL, p_samples=10)
        )
        captured = self._fake_agent_loop(monkeypatch)
        monkeypatch.setattr("src.utils.routing_reason_score", lambda prompt: 1)

        await orchestrate("borderline prompt", mode="auto")

        assert captured["model"] == DEFAULT_CODING_MODEL

    @pytest.mark.asyncio
    async def test_non_borderline_scores_never_consult_the_advisor(self, monkeypatch):
        from src.utils import (
            DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL, orchestrate,
        )

        mock_prefers = AsyncMock(return_value=True)
        monkeypatch.setattr("src.utils.RoutingAdvisor.prefers_planning", mock_prefers)
        captured = self._fake_agent_loop(monkeypatch)

        # Score 0: coding model, advisor never asked.
        monkeypatch.setattr("src.utils.routing_reason_score", lambda prompt: 0)
        await orchestrate("simple prompt", mode="auto")
        assert captured["model"] == DEFAULT_CODING_MODEL
        mock_prefers.assert_not_awaited()

        # Score 2: planning model on the static heuristic, advisor never asked.
        monkeypatch.setattr("src.utils.routing_reason_score", lambda prompt: 2)
        await orchestrate("complex prompt", mode="auto")
        assert captured["model"] == DEFAULT_PLANNING_MODEL
        mock_prefers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_margin_env_controls_the_flip(self, monkeypatch):
        from src.utils import DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL, RoutingAdvisor

        advisor = RoutingAdvisor()
        advisor.inject_stats(
            _advisor_stats(
                DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL, p_rate=0.60, c_rate=0.50
            )
        )
        # 0.10 gap < default 0.15 margin → static prior.
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is False
        # Lowered margin admits the same gap.
        monkeypatch.setenv("UNIGROK_ADVISOR_MARGIN", "0.05")
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is True

    @pytest.mark.asyncio
    async def test_bypassed_under_testing_without_injection(self):
        """UNI_GROK_TESTING=1 (conftest) must keep the advisor fully inert:
        static prior, zero store reads."""
        from src.utils import DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL, RoutingAdvisor

        advisor = RoutingAdvisor()
        mock_store = MagicMock()
        mock_store.get_recent_model_stats = AsyncMock()

        assert await advisor.prefers_planning(
            mock_store, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is False
        mock_store.get_recent_model_stats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ttl_cache_reads_store_once(self, monkeypatch):
        """Within the TTL the aggregate is served from memory — the hot path
        performs zero extra DB reads."""
        from src.utils import DEFAULT_CODING_MODEL, DEFAULT_PLANNING_MODEL, RoutingAdvisor

        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        advisor = RoutingAdvisor()
        mock_store = MagicMock()
        mock_store.get_recent_model_stats = AsyncMock(
            return_value=_advisor_stats(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL)
        )

        first = await advisor.prefers_planning(
            mock_store, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        )
        second = await advisor.prefers_planning(
            mock_store, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        )

        assert first is second is True
        assert mock_store.get_recent_model_stats.await_count == 1

    @pytest.mark.asyncio
    async def test_get_recent_model_stats_aggregates_last_window(self, tmp_path):
        """The store query aggregates per plane/model over the most recent
        rows only (window limit respected)."""
        s = GrokSessionStore(db_path=tmp_path / "advisor_stats.db")
        try:
            for i in range(4):
                await s.save_task_memory(
                    prompt=f"planning task {i}", outcome_summary="ok",
                    plane="API", model="grok-4.3", profile="default",
                    success=1 if i < 3 else 0, latency=1.0, cost=0.04,
                )
            await s.save_task_memory(
                prompt="coding task", outcome_summary="ok",
                plane="API", model="grok-build-0.1", profile="default",
                success=0, latency=1.0, cost=0.01,
            )
            await s.save_task_memory(
                prompt="unverified newest task", outcome_summary="provider stopped",
                plane="API", model="grok-4.3", profile="default",
                success=None, latency=1.0, cost=9.99,
            )

            rows = await s.get_recent_model_stats(200)
            by_model = {r["model"]: r for r in rows}
            assert by_model["grok-4.3"]["samples"] == 4
            assert by_model["grok-4.3"]["success_rate"] == pytest.approx(0.75)
            assert by_model["grok-4.3"]["avg_cost"] == pytest.approx(0.04)
            assert by_model["grok-build-0.1"]["samples"] == 1

            # The evidence window excludes the newest unverified row and sees
            # the newest verified row (the coding failure).
            newest = await s.get_recent_model_stats(1)
            assert len(newest) == 1
            assert newest[0]["model"] == "grok-build-0.1"
        finally:
            await s.close()


# ─────────────────────────────────────────────────────────────────────────────
# WORKSPACE_ROOT override (container bind-mount support)
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkspaceRootOverride:
    """WORKSPACE_ROOT points file/git access at a bind mount (compose sets
    /workspace); unset keeps the repo containing src/ as the root."""

    def test_project_root_defaults_to_repo(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
        assert (PathResolver.get_project_root() / "pyproject.toml").exists()

    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        assert PathResolver.get_project_root() == tmp_path

    def test_blank_override_is_ignored(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", "   ")
        assert (PathResolver.get_project_root() / "src").exists()

    def test_validate_path_confines_to_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        inside = tmp_path / "a.txt"
        inside.write_text("x")
        assert PathResolver.validate_path("a.txt") == inside.resolve()
        with pytest.raises(PermissionError):
            PathResolver.validate_path("/etc/hosts")


# ─────────────────────────────────────────────────────────────────────────────
# Audit Fixes (In-Memory Database Connection Initialization)
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditInMemoryDb:
    @pytest.mark.asyncio
    async def test_in_memory_database_initialization_str(self):
        # Verify str(":memory:") can be initialized and queried without FileNotFoundError
        store = GrokSessionStore(db_path=":memory:")
        try:
            await store.save_session("in_mem_sess", api_thread_id="thread_in_mem", model="grok-4.3")
            sess = await store.get_session("in_mem_sess")
            assert sess is not None
            assert sess["api_thread_id"] == "thread_in_mem"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_in_memory_database_initialization_path(self):
        # Verify Path(":memory:") can be initialized and queried without FileNotFoundError
        store = GrokSessionStore(db_path=Path(":memory:"))
        try:
            await store.save_session("in_mem_sess_path", api_thread_id="thread_in_mem_path", model="grok-4.3")
            sess = await store.get_session("in_mem_sess_path")
            assert sess is not None
            assert sess["api_thread_id"] == "thread_in_mem_path"
        finally:
            await store.close()
