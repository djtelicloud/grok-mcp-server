from __future__ import annotations

from unigrok_public.openai_facade import (
    admit_openai,
    flatten_messages,
    models_list,
    walk_door,
)


def test_flatten_messages_joins_user_and_system() -> None:
    system, prompt = flatten_messages(
        [
            {"role": "system", "content": "Stay terse"},
            {"role": "user", "content": "ping"},
        ]
    )
    assert system == "Stay terse"
    assert prompt == "ping"


def test_empty_body_asks_intent_instead_of_400() -> None:
    adm = admit_openai({}, "")
    hop, _collapsed = walk_door(adm)
    assert adm["has_intent"] is False
    assert hop == "ask_intent"


def test_models_list_is_unigrok() -> None:
    listing = models_list()
    assert listing["data"][0]["id"] == "unigrok"
