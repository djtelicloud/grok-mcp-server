"""Hive vote accuracy field (acc 1–100) with legacy c 0–2 compatibility."""

from __future__ import annotations

from unigrok_public.harness import build_vote_prompt, parse_hive_vote


def test_parse_hive_vote_acc_primary() -> None:
    vote = parse_hive_vote('{"v":"pass","acc":88,"r":"none","f":"none","loc":"L1-L2"}')
    assert vote is not None
    assert vote["v"] == "pass"
    assert vote["acc"] == 88
    # Legacy c filled from acc when missing
    assert vote["c"] in {0, 1, 2}
    assert vote["c"] == 88 // 34  # 2


def test_parse_hive_vote_legacy_c_only() -> None:
    vote = parse_hive_vote('{"v":"fail","c":1,"r":"null deref","f":"guard","loc":"L3-L4"}')
    assert vote is not None
    assert vote["v"] == "fail"
    assert vote["c"] == 1
    assert vote["acc"] == 50  # c*50


def test_parse_hive_vote_legacy_c_zero_maps_acc_floor() -> None:
    vote = parse_hive_vote('{"v":"pass","c":0,"r":"none","f":"none","loc":"-"}')
    assert vote is not None
    assert vote["c"] == 0
    assert vote["acc"] == 1  # contract floor 1–100


def test_parse_hive_vote_acc_zero_clamped() -> None:
    vote = parse_hive_vote('{"v":"pass","acc":0,"r":"none","f":"none","loc":"-"}')
    assert vote is not None
    assert vote["acc"] == 1


def test_parse_hive_vote_acc_and_c_both() -> None:
    vote = parse_hive_vote(
        '{"v":"risk","acc":42,"c":2,"r":"edge","f":"clamp","loc":"-"}'
    )
    assert vote is not None
    assert vote["acc"] == 42
    assert vote["c"] == 2  # preserved when both present


def test_vote_prompt_mentions_acc() -> None:
    prompt = build_vote_prompt(
        "task",
        "def f():\n    return 1\n",
        {"id": "critic", "system": "You are a critic."},
    )
    assert '"acc"' in prompt or "acc is accuracy" in prompt
