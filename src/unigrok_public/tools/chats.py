"""Chats domain — session list/history/forget + chat context helpers (Phase 3 SRP)."""
from __future__ import annotations

from typing import Any


def build_chat_system_context(
    *,
    layer_block: str | None = None,
    knowledge_block: str | None = None,
) -> str | None:
    """Join optional layer identity + durable knowledge for chat memory floor."""
    parts: list[str] = []
    if layer_block:
        parts.append(layer_block)
    if knowledge_block:
        parts.append(knowledge_block)
    return "\n\n".join(parts) if parts else None


def list_sessions_body(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"sessions": sessions, "count": len(sessions)}


def session_history_body(
    *, session: str, messages: list[Any]
) -> dict[str, Any]:
    return {
        "session": session,
        "messages": messages,
        "count": len(messages),
    }


def forget_session_body(*, session: str, deleted: bool) -> dict[str, Any]:
    return {
        "session": session,
        "status": "deleted" if deleted else "not_found",
    }


def chat_tool_contract() -> dict[str, Any]:
    """Documented chat tool behavior for discover/runtime (not a live call)."""
    return {
        "name": "chat",
        "plane": "auto",
        "agentic": False,
        "max_turns": 1,
        "fallback_policy": "cross_plane",
        "allow_web": False,
        "allow_x_search": False,
        "allow_code": False,
        "memory_floor": "optional durable knowledge inject",
    }
