"""Context pack: inventory → persona votes → lead merge → PFC condenser."""

from __future__ import annotations

import pytest

from unigrok_public.context_pack import (
    KnowledgePoint,
    _needs_second_loop,
    build_context_pack,
    context_pack_mode,
    format_session_with_pack,
    inventory,
    lead_merge,
    prefrontal_condense,
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


def test_prefrontal_one_loop_when_selection_is_thin() -> None:
    pack = lead_merge(
        inventory(
            [
                {
                    "role": "user",
                    "content": "Ship PFC. Never invent private intelligence.",
                }
            ],
            next_task="Ship PFC condenser",
        ),
        next_task="Ship PFC condenser",
    )
    # Thin selection: dont + maybe one keep → loop1 should seal.
    out = prefrontal_condense(pack, next_task="Ship PFC condenser")
    assert out.prefrontal
    assert out.pfc_loops == 1
    assert out.pfc_points >= 1
    assert "Avoid:" in out.prefrontal or "Hold:" in out.prefrontal or "Aim:" in out.prefrontal


def test_prefrontal_placement_and_loop2_gate() -> None:
    history = [
        {
            "role": "user",
            "content": (
                "Goal: ship prefrontal condenser. Never invent private intelligence. "
                "Do not read host IDE files. Avoid force-push. Must keep don'ts. "
                "Should commit only when asked."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Implemented inventory, lead merge, and planned PFC hive loops. "
                "Need Docker live defaults and tests for literal CommitDone."
            ),
        },
        {
            "role": "user",
            "content": "Also never place XAI_API_KEY in IDE MCP JSON.",
        },
        {
            "role": "assistant",
            "content": "Confirmed secrets stay in .env only; pack stores redacted text.",
        },
    ]
    pack = lead_merge(
        inventory(history, next_task="Ship densest PFC path to Docker"),
        next_task="Ship densest PFC path to Docker",
    )
    out = prefrontal_condense(pack, next_task="Ship densest PFC path to Docker")
    assert out.prefrontal
    assert out.pfc_loops in {1, 2}
    assert out.pfc_confidence > 0
    # Loop1 may seal when votes+sentence land in one turn; loop2 only if incomplete.
    assert _needs_second_loop(
        [],
        pack=pack,
        confidence=0.2,
        provisional="",
    )
    thin = [
        KnowledgePoint(
            text="thin keep",
            kind="keep",
            weight=0.4,
            votes={
                "critic": "keep",
                "bounty": "skip",
                "spec": "skip",
                "failures": "skip",
                "complexity": "skip",
            },
        )
    ]
    assert _needs_second_loop(
        thin,
        pack=pack,
        confidence=0.4,
        provisional="Hold: thin keep.",
    )
    rendered = format_session_with_pack(history, "Continue", out)
    assert "Prefrontal" in rendered
    # PFC must sit after pack body and before the current request (bottom cue).
    assert rendered.index("Prefrontal") < rendered.index("Current user request")
    assert rendered.index("Don'ts") < rendered.index("Prefrontal")


def test_prefrontal_runs_loop2_when_first_pass_incomplete() -> None:
    # Lead selection with don'ts but empty provisional forces loop2.
    pack = lead_merge(
        inventory(
            [
                {
                    "role": "user",
                    "content": "Never invent private intelligence. Do not read host files.",
                },
                {"role": "assistant", "content": "Noted constraints."},
            ],
            next_task="Continue safely",
        ),
        next_task="Continue safely",
    )
    # Monkey-patch compose to fail once — simulate incomplete loop1 sentence.
    import unigrok_public.context_pack as cp

    calls = {"n": 0}
    real = cp._compose_prefrontal

    def flaky(points, *, next_task: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return ""
        return real(points, next_task=next_task)

    cp._compose_prefrontal = flaky  # type: ignore[assignment]
    try:
        out = prefrontal_condense(pack, next_task="Continue safely")
    finally:
        cp._compose_prefrontal = real  # type: ignore[assignment]
    assert out.pfc_loops == 2
    assert out.prefrontal


def test_build_context_pack_includes_prefrontal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_CONTEXT_PACK", "cpu")
    pack = build_context_pack(
        session="s1",
        history=[
            {
                "role": "user",
                "content": "Remember: do not leak secrets. Ship the condenser.",
            },
            {"role": "assistant", "content": "Understood; packing context."},
        ],
        next_task="Continue without leaking secrets",
    )
    assert pack is not None
    assert pack.prefrontal
    assert pack.pfc_loops >= 1


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
    assert pack.prefrontal
    await store.save_context_pack("demo", pack.to_dict(), version=pack.version)
    loaded = await store.load_context_pack("demo")
    assert loaded is not None
    assert loaded.get("prefrontal")
    assert loaded.get("pfc_loops", 0) >= 1
