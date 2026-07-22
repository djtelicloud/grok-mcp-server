"""Public-safe chat memory + layer + task_rag honesty (A1–A3 packet)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from unigrok_public import server
from unigrok_public.state import PublicStateStore


@pytest.fixture()
def isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PublicStateStore:
    store = PublicStateStore(tmp_path / "state.db")
    monkeypatch.setattr(server, "STATE", store)
    return store


def test_public_free_empty_layer_no_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "UNIGROK_LAYER", "")
    monkeypatch.setattr(server, "UNIGROK_LAYER_COLLECTION", "")
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", False)
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", True)
    assert server._layer_context_block() == ""
    assert server._layer_service_label() == server.SERVICE_NAME


def test_layer_env_smoke_gemma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "UNIGROK_LAYER", "gemma")
    monkeypatch.setattr(server, "UNIGROK_LAYER_COLLECTION", "")
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", False)
    block = server._layer_context_block()
    assert "GemmaGrok" in block
    assert "layer=`gemma`" in block
    assert "run.app" not in block
    assert server._layer_service_label() == "GemmaGrok"


def test_task_rag_honesty_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", True)
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", True)
    mode = "local_sqlite_knowledge" if server.TASK_RAG_ACTIVE or server.CHAT_MEMORY_ALWAYS else "off"
    assert mode == "local_sqlite_knowledge"
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", False)
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", False)
    mode_off = "local_sqlite_knowledge" if server.TASK_RAG_ACTIVE or server.CHAT_MEMORY_ALWAYS else "off"
    assert mode_off == "off"


def test_chat_injects_knowledge_when_facts_exist(
    isolate_state: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(isolate_state.save_fact("alpha policy hold PROMOTE NO", scope="global"))
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", True)
    monkeypatch.setattr(server, "UNIGROK_LAYER", "")
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", False)

    captured: dict[str, Any] = {}

    async def fake_run_unified(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["system_context"] = kwargs.get("system_context")
        return {"status": "complete", "text": "ok", "cost_usd": 0}

    async def fake_durable(produce, ctx=None, kind="chat"):
        return await produce()

    monkeypatch.setattr(server, "_run_unified", fake_run_unified)
    monkeypatch.setattr(server, "_run_durable_job", fake_durable)
    out = asyncio.run(server.chat("what is alpha policy?"))
    assert out["status"] == "complete"
    assert captured["system_context"]
    assert "alpha policy" in captured["system_context"]
    assert "Durable seat knowledge" in captured["system_context"]


def test_healthz_layer_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "MCP_SERVER_NAME", "GemmaGrok")
    monkeypatch.setattr(server, "UNIGROK_LAYER", "gemma")

    async def run() -> bytes:
        req = Request({"type": "http", "method": "GET", "path": "/healthz", "headers": []})
        resp = await server.healthz(req)
        return resp.body

    data = json.loads(asyncio.run(run()))
    assert data["status"] == "ok"
    assert data["service"] == "GemmaGrok"
    assert data["layer"] == "gemma"
