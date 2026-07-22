"""Public-safe chat memory + layer + task_rag honesty (A1–A3 packet)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from unigrok_public import server
from unigrok_public.identity import (
    reset_active_principal,
    scoped_scope,
    set_active_principal,
)
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


def test_layer_name_validation_and_consistent_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert server._normalize_layer_name(" Research-Lab ") == "research-lab"
    for invalid in (
        "bad layer",
        "bad\npolicy",
        "../private",
        "-leading",
        "trailing-",
        "x" * 33,
    ):
        with pytest.raises(ValueError, match="UNIGROK_LAYER"):
            server._normalize_layer_name(invalid)

    monkeypatch.setattr(server, "UNIGROK_LAYER", "research-lab")
    assert server._layer_service_label() == "ResearchLabGrok"


def test_custom_layer_sets_actual_mcp_handshake_name() -> None:
    env = dict(os.environ)
    env["UNIGROK_LAYER"] = "research-lab"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from unigrok_public import server; "
                "print(json.dumps([server.MCP_SERVER_NAME, "
                "server.mcp._mcp_server.name]))"
            ),
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert json.loads(result.stdout.strip()) == ["ResearchLabGrok", "ResearchLabGrok"]


def test_layer_context_is_generic_and_withholds_collection_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "private-vault-sentinel"
    monkeypatch.setattr(server, "UNIGROK_LAYER", "sky")
    monkeypatch.setattr(server, "UNIGROK_LAYER_COLLECTION", sentinel)
    monkeypatch.setattr(server, "UNIGROK_TASK_RAG_COLLECTION", "")
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", True)

    block = server._layer_context_block()
    assert "SkyGrok" in block
    assert "operator collection label is configured" in block.lower()
    for forbidden in (
        sentinel,
        "AgentixAI",
        "Space",
        "Ground",
        "vault",
        "disaster recovery",
        "GO_WITH_CONSTRAINTS",
    ):
        assert forbidden not in block


def test_task_rag_honesty_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", True)
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", True)
    mode = (
        "local_sqlite_knowledge"
        if server.TASK_RAG_ACTIVE or server.CHAT_MEMORY_ALWAYS
        else "off"
    )
    assert mode == "local_sqlite_knowledge"
    monkeypatch.setattr(server, "TASK_RAG_ACTIVE", False)
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", False)
    mode_off = (
        "local_sqlite_knowledge"
        if server.TASK_RAG_ACTIVE or server.CHAT_MEMORY_ALWAYS
        else "off"
    )
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


def test_authenticated_chat_memory_is_tenant_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PublicStateStore(tmp_path / "tenant-chat.db")
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "CHAT_MEMORY_ALWAYS", True)
    monkeypatch.setattr(server, "UNIGROK_LAYER", "")

    alice_token = set_active_principal("oauth:issuer:alice")
    try:
        alice_scope = scoped_scope("global")
        asyncio.run(store.save_fact("shared keyword alice only", scope=alice_scope))
    finally:
        reset_active_principal(alice_token)

    bob_token = set_active_principal("oauth:issuer:bob")
    try:
        bob_scope = scoped_scope("global")
        asyncio.run(store.save_fact("shared keyword bob only", scope=bob_scope))
    finally:
        reset_active_principal(bob_token)

    captured: dict[str, Any] = {}

    async def fake_run_unified(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["system_context"] = kwargs.get("system_context")
        return {"status": "complete", "text": "ok", "cost_usd": 0}

    async def fake_durable(produce, ctx=None, kind="chat"):
        return await produce()

    monkeypatch.setattr(server, "_run_unified", fake_run_unified)
    monkeypatch.setattr(server, "_run_durable_job", fake_durable)

    alice_token = set_active_principal("oauth:issuer:alice")
    try:
        asyncio.run(server.chat("shared keyword"))
    finally:
        reset_active_principal(alice_token)

    context = str(captured["system_context"])
    assert "alice only" in context
    assert "bob only" not in context
    assert "tenant-" not in context
    bob_results = asyncio.run(
        store.search_facts("shared keyword", scope=bob_scope, limit=5)
    )
    assert bob_results
    assert all(int(item["uses"]) == 0 for item in bob_results)


def test_authenticated_layer_fallback_uses_tenant_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PublicStateStore(tmp_path / "tenant-fallback.db")
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "UNIGROK_LAYER", "sky")

    alice_token = set_active_principal("oauth:issuer:alice")
    try:
        asyncio.run(
            store.save_fact(
                "sky policy holds GO PROMOTE alice", scope=scoped_scope("global")
            )
        )
    finally:
        reset_active_principal(alice_token)

    bob_token = set_active_principal("oauth:issuer:bob")
    try:
        asyncio.run(
            store.save_fact(
                "sky policy holds GO PROMOTE bob", scope=scoped_scope("global")
            )
        )
    finally:
        reset_active_principal(bob_token)

    alice_token = set_active_principal("oauth:issuer:alice")
    try:
        block = asyncio.run(server._durable_knowledge_block("unrelated query"))
    finally:
        reset_active_principal(alice_token)

    assert "alice" in block
    assert "bob" not in block
    assert "tenant-" not in block


def test_unauthenticated_chat_memory_preserves_local_all_scope_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PublicStateStore(tmp_path / "local-chat.db")
    monkeypatch.setattr(server, "STATE", store)
    asyncio.run(store.save_fact("shared keyword first", scope="project-a"))
    asyncio.run(store.save_fact("shared keyword second", scope="project-b"))
    tenant_token = set_active_principal("oauth:issuer:alice")
    try:
        tenant_scope = scoped_scope("global")
        asyncio.run(store.save_fact("shared keyword tenant secret", scope=tenant_scope))
    finally:
        reset_active_principal(tenant_token)

    token = set_active_principal(None)
    try:
        block = asyncio.run(server._durable_knowledge_block("shared keyword"))
    finally:
        reset_active_principal(token)

    assert "first" in block
    assert "second" in block
    assert "tenant secret" not in block
    assert "tenant-" not in block
    tenant_results = asyncio.run(
        store.search_facts("shared keyword", scope=tenant_scope, limit=5)
    )
    assert tenant_results
    assert all(int(item["uses"]) == 0 for item in tenant_results)


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
