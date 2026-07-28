"""Phase 3 SRP: chats + system domain builders (no full server boot)."""
from __future__ import annotations

from unigrok_public.tools import chats, system


def test_healthz_body():
    body = system.healthz_body(service="UniGrok", version="9.9.9", layer="public")
    assert body["status"] == "ok"
    assert body["service"] == "UniGrok"
    assert body["version"] == "9.9.9"


def test_readyz_body_full_and_cloudrun():
    body, code = system.readyz_body(
        ready=True,
        catalogs={"cli": {"ready": True}},
        bootstrap={"can_chat": True},
        state_ready=True,
        cloudrun=False,
    )
    assert code == 200
    assert body["status"] == "ready"
    assert "planes" in body
    body2, code2 = system.readyz_body(
        ready=False,
        catalogs={},
        bootstrap={},
        state_ready=False,
        cloudrun=True,
    )
    assert code2 == 503
    assert body2 == {"status": "not_ready"}


def test_chat_context_and_sessions():
    assert chats.build_chat_system_context() is None
    ctx = chats.build_chat_system_context(layer_block="L", knowledge_block="K")
    assert ctx == "L\n\nK"
    sessions = [{"name": "a"}]
    assert chats.list_sessions_body(sessions)["count"] == 1
    hist = chats.session_history_body(session="s", messages=[1, 2])
    assert hist["count"] == 2
    assert chats.forget_session_body(session="s", deleted=True)["status"] == "deleted"
    assert chats.chat_tool_contract()["name"] == "chat"
