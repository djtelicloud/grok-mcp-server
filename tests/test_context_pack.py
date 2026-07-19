"""Context pack: inventory → persona votes → lead merge."""

from __future__ import annotations

import pytest

from unigrok_public.context_pack import (
    build_context_pack,
    context_pack_mode,
    format_session_with_pack,
    inventory,
    lead_merge,
)
from unigrok_public.state import PublicStateStore


def test_context_pack_mode_defaults_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNIGROK_CONTEXT_PACK", raising=False)
    assert context_pack_mode() == "off"
    monkeypatch.setenv("UNIGROK_CONTEXT_PACK", "cpu")
    assert context_pack_mode() == "cpu"
    monkeypatch.setenv("UNIGROK_CONTEXT_PACK", "hive")
    assert context_pack_mode() == "cpu"


def test_inventory_extracts_donts_and_scores() -> None:
    history = [
        {
            "role": "user",
            "content": "Implement literal CommitDone. Do not force-push main.",
        },
        {
            "role": "assistant",
            "content": "Added task_class literal matcher and tests.",
        },
    ]
    items = inventory(history, next_task="Fix literal mismatch on exactly: token")
    kinds = {item.kind for item in items}
    assert "turn_user" in kinds
    assert "turn_assistant" in kinds
    assert any(item.kind == "dont_candidate" for item in items)


def test_lead_merge_keeps_donts_and_useful_keeps() -> None:
    history = [
        {
            "role": "user",
            "content": (
                "Ship context pack prune. Never invent private intelligence. "
                "Goal: keep don'ts and useful keeps for next turn."
            ),
        },
        {
            "role": "assistant",
            "content": "Designed inventory, persona votes, and lead merge.",
        },
        {
            "role": "user",
            "content": "Also avoid reading host IDE files.",
        },
    ]
    items = inventory(history, next_task="Ship context pack to Docker")
    pack = lead_merge(items, next_task="Ship context pack to Docker")
    assert pack.donts, "expected at least one don't"
    assert pack.keeps, "expected keeps"
    assert pack.dropped >= 0
    rendered = format_session_with_pack(history, "Continue shipping", pack)
    assert "Don'ts" in rendered
    assert "Current user request" in rendered
    assert "Continue shipping" in rendered


def test_build_context_pack_respects_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_CONTEXT_PACK", "off")
    assert (
        build_context_pack(
            session="s1",
            history=[{"role": "user", "content": "hello"}],
            next_task="hi",
        )
        is None
    )


@pytest.mark.asyncio
async def test_context_pack_roundtrip_sqlite(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNIGROK_CONTEXT_PACK", "cpu")
    store = PublicStateStore(tmp_path / "ctx.db")
    await store.initialize()
    await store.append_turn("demo", "Do not leak secrets.", "Understood.")
    history = await store.load_messages("demo")
    pack = build_context_pack(
        session="demo",
        history=history,
        next_task="Continue without leaking secrets",
        version=1,
    )
    assert pack is not None
    await store.save_context_pack("demo", pack.to_dict(), version=pack.version)
    loaded = await store.load_context_pack("demo")
    assert loaded is not None
    assert loaded.get("donts") or loaded.get("keeps")
