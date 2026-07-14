"""Normalized Gemini generateContent request and response helpers."""

from __future__ import annotations

from typing import Any

from .contracts import ProviderMessage, ProviderRequest
from .errors import ProviderProtocolError


_FILTER_REASONS = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "IMAGE_SAFETY",
}


def build_generate_content_payload(
    request: ProviderRequest,
    *,
    max_output_tokens: int,
    messages: list[ProviderMessage] | None = None,
) -> dict[str, Any]:
    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    for message in messages if messages is not None else request.messages:
        if message.role == "system":
            system_parts.append({"text": message.content})
            continue
        contents.append(
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
        )
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "candidateCount": 1,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    if request.temperature is not None:
        payload["generationConfig"]["temperature"] = request.temperature
    return payload


def parse_generate_content_response(
    provider,
    data: dict[str, Any],
) -> tuple[str, str, Any, Any, Any, Any, Any]:
    candidates = data.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates else None
    finish_raw = ""
    parts: list[Any] = []
    if isinstance(candidate, dict):
        finish_raw = str(candidate.get("finishReason") or "").upper()
        content = candidate.get("content")
        if isinstance(content, dict) and isinstance(content.get("parts"), list):
            parts = content["parts"]
    text_parts = [
        str(part.get("text"))
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
        and not bool(part.get("thought"))
    ]
    text = "".join(text_parts)
    prompt_feedback = data.get("promptFeedback")
    block_reason = (
        str(prompt_feedback.get("blockReason") or "").upper()
        if isinstance(prompt_feedback, dict)
        else ""
    )
    if finish_raw == "MAX_TOKENS":
        finish = "length"
    elif finish_raw in _FILTER_REASONS or block_reason:
        finish = "content_filter"
    elif finish_raw in {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}:
        finish = "tool_calls"
    elif finish_raw in {"STOP", "FINISH_REASON_UNSPECIFIED"} and text:
        finish = "stop"
    else:
        finish = "unknown"
    if not text and finish != "content_filter":
        raise ProviderProtocolError(provider, "missing_text")
    usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    return (
        text,
        finish,
        data.get("modelVersion"),
        data.get("responseId"),
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        usage.get("totalTokenCount"),
    )
