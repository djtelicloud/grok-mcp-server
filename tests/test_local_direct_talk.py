"""Non-certified named-peer direct-talk mode."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from unigrok_public import server


def _reload_with(monkeypatch, env: dict[str, str]):
    for key in (
        "UNIGROK_LAYER",
        "UNIGROK_LOCAL_DIRECT_TALK_MODE",
        "UNIGROK_LOCAL_DIRECT_MODEL",
        "UNIGROK_LOCAL_RUNTIME_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(server)


@pytest.fixture(autouse=True)
def _restore_server():
    yield
    importlib.reload(server)


def test_direct_talk_off_by_default(monkeypatch):
    current = _reload_with(monkeypatch, {})
    assert current.DIRECT_TALK_ACTIVE is False
    assert current.MCP_SERVER_NAME == current.SERVICE_NAME


def test_direct_talk_requires_full_combo(monkeypatch):
    assert _reload_with(monkeypatch, {"UNIGROK_LAYER": "gemma"}).DIRECT_TALK_ACTIVE is False
    assert (
        _reload_with(
            monkeypatch,
            {
                "UNIGROK_LAYER": "gemma",
                "UNIGROK_LOCAL_DIRECT_TALK_MODE": "non_certified",
            },
        ).DIRECT_TALK_ACTIVE
        is False
    )
    assert (
        _reload_with(
            monkeypatch,
            {
                "UNIGROK_LAYER": "sky",
                "UNIGROK_LOCAL_DIRECT_TALK_MODE": "non_certified",
                "UNIGROK_LOCAL_DIRECT_MODEL": "gemma4",
                "UNIGROK_LOCAL_RUNTIME_URL": "http://rt.test",
            },
        ).DIRECT_TALK_ACTIVE
        is False
    )


def _active(monkeypatch):
    return _reload_with(
        monkeypatch,
        {
            "UNIGROK_LAYER": "gemma",
            "UNIGROK_LOCAL_DIRECT_TALK_MODE": "non_certified",
            "UNIGROK_LOCAL_DIRECT_MODEL": "gemma4",
            "UNIGROK_LOCAL_RUNTIME_URL": "http://rt.test",
        },
    )


def test_direct_talk_active_names_peer_from_layer(monkeypatch):
    current = _active(monkeypatch)
    assert current.DIRECT_TALK_ACTIVE is True
    assert current.MCP_SERVER_NAME == "GemmaGrok"


def test_run_unified_direct_talk_never_resolves_plane(monkeypatch):
    current = _active(monkeypatch)

    async def _boom(*args, **kwargs):
        raise AssertionError("_resolve_plane must not run in direct-talk mode")

    monkeypatch.setattr(current, "_resolve_plane", _boom)

    async def _fake_transport(
        base, model, messages, *, max_tokens, timeout  # noqa: ASYNC109
    ):
        assert base == "http://rt.test"
        assert model == "gemma4"
        return {"text": "hello from gemma", "stop_reason": "stop"}

    monkeypatch.setattr(current, "_openai_compat_chat", _fake_transport)
    result = asyncio.run(
        current._run_unified(
            "hi",
            model=None,
            effort=None,
            plane="auto",
            fallback_policy="cross_plane",
            agentic=False,
            max_turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
        )
    )
    assert result["text"] == "hello from gemma"
    assert result["certification_status"] == "NON_CERTIFIED"
    assert result["failover_eligible"] is False
    assert result["gate_id"] is None
    assert result["all_traffic_abstain"] == "OPEN"
    assert result["plane"] == "local"
    assert result["resolved_plane"] == "local"
    assert result["route_mode"] == "direct_talk"
    assert result["floor_role"] is None
    assert result["floor_metric_ids"] == []
    assert result["cost_usd"] == 0.0


def test_direct_talk_capabilities_fail_closed(monkeypatch):
    current = _active(monkeypatch)

    async def _boom(*args, **kwargs):
        raise AssertionError("transport must not run for a capability request")

    monkeypatch.setattr(current, "_openai_compat_chat", _boom)
    result = asyncio.run(
        current._serve_local_direct_noncertified("do a thing", allow_web=True)
    )
    assert result["status"] == "error"
    assert result["stop_reason"] == "direct_talk_unsupported_capability"
    assert result["certification_status"] == "NON_CERTIFIED"
    assert result["model"] is None


def test_direct_talk_runtime_error_fails_closed(monkeypatch):
    current = _active(monkeypatch)

    async def _explode(*args, **kwargs):
        raise RuntimeError("runtime down")

    monkeypatch.setattr(current, "_openai_compat_chat", _explode)
    result = asyncio.run(current._serve_local_direct_noncertified("hi"))
    assert result["status"] == "error"
    assert result["stop_reason"] == "local_runtime_unavailable"
    assert result["failover_eligible"] is False
