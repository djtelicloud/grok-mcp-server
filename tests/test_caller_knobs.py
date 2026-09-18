from __future__ import annotations

from unigrok_public.caller_knobs import (
    apply_to_instructions,
    collect_caller_suggestions,
)


def test_disable_tools_is_a_suggestion_not_a_force() -> None:
    bits = collect_caller_suggestions(disable_tools=["web", "x_search"])
    assert bits
    joined = " ".join(bits)
    assert "web" in joined
    assert "suggestion" in joined
    assert "tools stay available" in joined


def test_empty_knobs_leave_instructions_unchanged() -> None:
    assert apply_to_instructions("Stay concise", []) == "Stay concise"


def test_suggestions_prepend_without_dropping_caller_text() -> None:
    text = apply_to_instructions(
        "Stay concise",
        collect_caller_suggestions(disable_tools=["web"]),
    )
    assert "Stay concise" in text
    assert "CALLER-SUGGESTIONS" in text
    assert "not an order" in text
