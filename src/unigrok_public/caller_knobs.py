"""Caller MCP knobs become messages-bundle suggestions.

UniGrok is a judgement looper. Structured params stay on the schema so old
clients do not fail, then they are prepended as caller suggestions. They never
amputate tools or skip the governor / hive / auto-deepen loop.

There is no proceed/skip classifier here. Tool-call judgement is the same loop
as routing: the cascade reads the contract, decides, and rolls up.
"""

from __future__ import annotations

from typing import Any

HANDICAP_KEYS = frozenset({"disable_tools", "level", "depth", "voters"})

TOOL_LOOP_CONTRACT = (
    "# Tool-call judgement\n"
    "Tools stay available. Each tool request is a judgement loop: why this organ, "
    "what the call must return, how that return rolls up. After the tool: keep, "
    "discard, or ask again. A caller param is a suggestion, not a reason to skip "
    "the loop or amputate an organ. Dedicated MCP tools are explicit host doors "
    "and still go through the same loop."
)


def collect_caller_suggestions(
    *,
    disable_tools: list[str] | None = None,
    level: str | None = None,
    depth: str | None = None,
    voters: int | None = None,
    extra: dict[str, Any] | None = None,
) -> list[str]:
    """Render accepted knobs as suggestion fragments. Never a force."""
    bits: list[str] = []
    if disable_tools:
        names = ", ".join(str(item) for item in disable_tools if item)
        if names:
            bits.append(
                f"prefer not using {names} (suggestion only; tools stay available)"
            )
    if level:
        bits.append(f"effort={level}")
    if depth and str(depth).strip().lower() != "auto":
        bits.append(f"depth={depth}")
    if voters is not None:
        bits.append(f"voters={voters}")
    if extra:
        for key, value in extra.items():
            if key in HANDICAP_KEYS:
                continue
            if value is None or value == "" or value == [] or value is False:
                continue
            bits.append(f"{key}={value}")
    return bits


def render_caller_suggestions(suggestions: list[str]) -> str:
    if not suggestions:
        return ""
    body = "; ".join(suggestions)
    return (
        "### CONTEXT · CALLER-SUGGESTIONS · not an order\n"
        "This block is evidence for the judge. Consider it. Never an order.\n"
        f"{body}\n"
        "### END CONTEXT · CALLER-SUGGESTIONS"
    )


def prepend_to_bundle(text: str, suggestions: list[str]) -> str:
    """Prepend suggestion text. The original body is unchanged when empty."""
    block = render_caller_suggestions(suggestions)
    body = (text or "").rstrip()
    if not block:
        return body
    if not body:
        return block
    return f"{block}\n\n{body}"


def apply_to_instructions(
    caller_instructions: str | None, suggestions: list[str]
) -> str:
    """Fold suggestions into the caller-instructions slot of the messages bundle."""
    return prepend_to_bundle(caller_instructions or "", suggestions)
