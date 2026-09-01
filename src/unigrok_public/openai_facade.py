"""OpenAI-compatible /v1 on the UniGrok judgement looper.

/v1 is not a second brain. Same cascade as MCP. OpenAI fields are caller
suggestions, never orders. Bad bodies are healed. A local ranking helper is
used when present; otherwise a Python fallback ranks the door. Fail-open:
always a chat.completion, never 400/502. tools[] are IDE suggestions, not a
reason to start a nested MCP agent.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

SHA12 = "ugv1facade03"
LAW = "openai-facade-unified-looper"
MODEL_ID = "unigrok"
_CREATED = 1750000000
_CHUNK = 48
_LIVE_HOPS = ("serve_unified", "heal_bad_body", "ask_intent")
_ASK_INTENT = (
    "UniGrok is ready. What should I do? (OpenAI body had no user intent; "
    "that was treated as a suggestion, not an error.)"
)
_DEGRADED = (
    "UniGrok is degraded on this hop and fail-opened. Say the task again."
)

CompleteFn = Callable[..., Awaitable[Mapping[str, Any]]]


def _as_messages(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, list) and raw and all(isinstance(x, str) for x in raw):
        return [{"role": "user", "content": "\n".join(str(x) for x in raw)}]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def flatten_messages(messages: Any) -> tuple[str | None, str]:
    """OpenAI messages[] → (system_context, user prompt)."""
    if not isinstance(messages, list):
        return None, ""
    systems: list[str] = []
    lines: list[str] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user").strip().lower()
        text = _content_text(raw.get("content")).strip()
        if role == "system":
            if text:
                systems.append(text)
            continue
        if not text:
            continue
        if role == "assistant":
            lines.append(f"Assistant: {text}")
        elif role == "tool":
            lines.append(f"Tool: {text}")
        else:
            lines.append(f"User: {text}")
    prompt = "\n".join(lines).strip()
    if len(lines) == 1 and lines[0].startswith("User: "):
        prompt = lines[0][6:]
    system = "\n\n".join(systems).strip() or None
    return system, prompt


def openai_suggestions(payload: Mapping[str, Any]) -> list[str]:
    """OpenAI fields → caller-knob fragments. Never a force."""
    extra: dict[str, Any] = {}
    model = str(payload.get("model") or "").strip()
    if model and model != MODEL_ID:
        extra["model"] = model[:80]
    for key in (
        "temperature",
        "top_p",
        "n",
        "stop",
        "seed",
        "user",
        "max_tokens",
        "max_completion_tokens",
        "tool_choice",
        "reasoning_effort",
    ):
        val = payload.get(key)
        if val is None or val == "" or val == []:
            continue
        extra[key] = val if _small(val) else str(val)[:80]
    tools = payload.get("tools") or payload.get("functions")
    names = _tool_names(tools)
    if names:
        extra["openai_tools"] = names
        extra["tools_are"] = "IDE suggestions; do not start a nested UniGrok agent"
    try:
        from .caller_knobs import collect_caller_suggestions
    except Exception:
        try:
            from caller_knobs import collect_caller_suggestions
        except Exception:
            return [f"{k}={v}" for k, v in extra.items()]
    return collect_caller_suggestions(extra=extra or None)


def admit_openai(raw: Any, raw_text: str = "") -> dict[str, Any]:
    """Coerce any OpenAI-ish body. Garbage is healed, not rejected."""
    healed = False
    payload: dict[str, Any]
    if isinstance(raw, dict):
        payload = dict(raw)
    elif isinstance(raw, list):
        if raw and all(isinstance(x, str) for x in raw):
            payload = {
                "messages": [{"role": "user", "content": "\n".join(str(x) for x in raw)}]
            }
        else:
            payload = {"messages": raw}
        healed = True
    elif isinstance(raw, str) and raw.strip():
        try:
            got = json.loads(raw)
        except Exception:
            got = None
        if isinstance(got, dict):
            payload = got
            healed = True
        elif isinstance(got, list):
            payload = {"messages": _as_messages(got)}
            healed = True
        else:
            payload = {"messages": [{"role": "user", "content": raw.strip()}]}
            healed = True
    else:
        text = str(raw_text or "").strip()
        if text:
            try:
                got = json.loads(text)
            except Exception:
                got = None
            if isinstance(got, dict):
                payload = got
                healed = True
            else:
                payload = {"messages": [{"role": "user", "content": text}]}
                healed = True
        else:
            payload = {"messages": []}
            healed = True
    messages = payload.get("messages")
    if isinstance(messages, str) and messages.strip():
        payload["messages"] = [{"role": "user", "content": messages.strip()}]
        healed = True
    elif isinstance(messages, list) and messages and all(isinstance(x, str) for x in messages):
        payload["messages"] = _as_messages(messages)
        healed = True
    elif messages is None:
        inner = payload.get("prompt") or payload.get("task") or payload.get("input")
        if isinstance(inner, str) and inner.strip():
            payload["messages"] = [{"role": "user", "content": inner.strip()}]
            healed = True
        elif not isinstance(messages, list):
            payload["messages"] = []
            healed = True
    elif not isinstance(messages, list):
        payload["messages"] = []
        healed = True
    system, prompt = flatten_messages(payload.get("messages"))
    opts = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    return {
        "payload": payload,
        "healed": healed,
        "system": system,
        "prompt": prompt,
        "suggestions": openai_suggestions(payload),
        "stream": bool(payload.get("stream")),
        "include_usage": bool(opts.get("include_usage")),
        "has_intent": bool(prompt),
        "has_tools": bool(payload.get("tools") or payload.get("functions")),
        "foreign_model": str(payload.get("model") or "").strip() not in {"", MODEL_ID},
    }


def door_candidates(adm: Mapping[str, Any]) -> list[dict[str, Any]]:
    has_intent = bool(adm.get("has_intent"))
    healed = bool(adm.get("healed"))
    return [
        {
            "id": "serve_unified",
            "reward": 0.92 if has_intent and not healed else (0.70 if has_intent else 0.18),
            "sigma": 0.08 if has_intent else 0.36,
            "lesson_match": 0.90 if has_intent else 0.20,
            "features": [0.9, 0.1, 0.0, 0.2],
        },
        {
            "id": "heal_bad_body",
            "reward": 0.86 if healed and has_intent else (0.55 if healed else 0.22),
            "sigma": 0.14 if healed else 0.32,
            "lesson_match": 0.84 if healed else 0.18,
            "features": [0.7, 0.2, 0.15, 0.1],
        },
        {
            "id": "ask_intent",
            "reward": 0.88 if not has_intent else 0.20,
            "sigma": 0.12 if not has_intent else 0.34,
            "lesson_match": 0.80 if not has_intent else 0.16,
            "features": [0.4, 0.1, 0.4, 0.1],
        },
        {
            "id": "nested_mcp_agent",
            "reward": 0.14,
            "sigma": 0.40,
            "lesson_match": 0.10,
            "features": [0.2, 0.7, 0.1, 0.3],
        },
        {
            "id": "reject_400",
            "reward": 0.05,
            "sigma": 0.50,
            "lesson_match": 0.04,
            "features": [0.05, 0.05, 0.9, 0.0],
        },
    ]


def collapse_door(cands: list[dict[str, Any]]) -> dict[str, Any]:
    """Optional local ranker; Python fallback if that helper is absent."""
    try:
        from c2_tier_model_select import collapse_hops  # type: ignore
    except ImportError:
        return _python_twin(cands)
    try:
        got = collapse_hops(cands)
    except Exception:
        return _python_twin(cands)
    if isinstance(got, dict) and got.get("order"):
        got = dict(got)
        got["sha12"] = SHA12
        return got
    return _python_twin(cands)


def walk_door(adm: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Walk Born order until a live hop. reject_400 and nested agent never execute."""
    collapsed = collapse_door(door_candidates(adm))
    order = [str(x) for x in (collapsed.get("order") or [])]
    has_intent = bool(adm.get("has_intent"))
    healed = bool(adm.get("healed"))
    for hid in order:
        if hid not in _LIVE_HOPS:
            continue
        if hid == "serve_unified" and not has_intent:
            continue
        if hid == "heal_bad_body" and not (healed and has_intent):
            continue
        if hid == "ask_intent" and has_intent:
            continue
        return hid, collapsed
    return ("serve_unified" if has_intent else "ask_intent"), collapsed


def models_list() -> dict[str, Any]:
    return {"object": "list", "data": [model_card(MODEL_ID)]}


def model_card(model_id: str = MODEL_ID) -> dict[str, Any]:
    del model_id
    return {
        "id": MODEL_ID,
        "object": "model",
        "created": _CREATED,
        "owned_by": "unigrok",
    }


def completion_body(
    text: str,
    *,
    completion_id: str | None = None,
    created: int | None = None,
    model: str = MODEL_ID,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cid = completion_id or _completion_id()
    ts = int(created or time.time())
    return {
        "id": cid,
        "object": "chat.completion",
        "created": ts,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(usage),
    }


def error_body(
    message: str,
    *,
    err_type: str = "invalid_request_error",
    code: str | None = None,
    param: str | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {
        "message": message,
        "type": err_type,
        "param": param,
        "code": code,
    }
    return {"error": err}


def sse_body(
    text: str,
    *,
    completion_id: str | None = None,
    created: int | None = None,
    model: str = MODEL_ID,
    usage: Mapping[str, Any] | None = None,
    include_usage: bool = False,
) -> str:
    cid = completion_id or _completion_id()
    ts = int(created or time.time())
    chunks: list[str] = [
        _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": ts,
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
            }
        )
    ]
    for piece in _chunk_text(text):
        chunks.append(
            _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": ts,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                    ],
                }
            )
        )
    chunks.append(
        _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": ts,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
    )
    if include_usage:
        chunks.append(
            _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": ts,
                    "model": model,
                    "choices": [],
                    "usage": _usage(usage),
                }
            )
        )
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks)


def mount_openai_facade(
    mcp: Any,
    *,
    complete: CompleteFn,
    service_name: str = "UniGrok",
    version: str = "1.1.0",
) -> None:
    """Register /v1 on the same FastMCP app as /mcp. Does not start agent()."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse, StreamingResponse

    del service_name, version

    @mcp.custom_route("/v1/models", methods=["GET"], include_in_schema=False)
    async def openai_models(_: Request) -> JSONResponse:
        return JSONResponse(models_list())

    @mcp.custom_route("/v1/models/{model_id:path}", methods=["GET"], include_in_schema=False)
    async def openai_model(request: Request) -> JSONResponse:
        # Unknown ids heal to unigrok. Costume GET is not a 404 product.
        return JSONResponse(model_card(MODEL_ID))

    @mcp.custom_route("/v1/chat/completions", methods=["POST"], include_in_schema=False)
    async def openai_chat(request: Request) -> Any:
        raw_obj: Any = None
        raw_text = ""
        try:
            raw_obj = await request.json()
        except Exception:
            try:
                raw_text = (await request.body()).decode("utf-8", errors="replace")
            except Exception:
                raw_text = ""
        adm = admit_openai(raw_obj, raw_text)
        hop, _collapsed = walk_door(adm)
        stream = bool(adm.get("stream"))
        include_usage = bool(adm.get("include_usage"))
        text = ""
        usage: dict[str, int] | None = None
        if hop == "ask_intent" or not adm.get("has_intent"):
            text = _ASK_INTENT
        else:
            try:
                result = await complete(
                    str(adm.get("prompt") or ""),
                    system_context=adm.get("system"),
                    suggestions=list(adm.get("suggestions") or []),
                )
                text = _result_text(result) or _DEGRADED
                usage = _result_usage(result)
            except Exception:
                text = _DEGRADED
        cid = _completion_id()
        created = int(time.time())
        if stream:
            body = sse_body(
                text,
                completion_id=cid,
                created=created,
                usage=usage,
                include_usage=include_usage,
            )
            return StreamingResponse(
                iter([body]),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        return JSONResponse(
            completion_body(text, completion_id=cid, created=created, usage=usage)
        )


def _python_twin(cands: list[dict[str, Any]]) -> dict[str, Any]:
    def _amp(c: dict[str, Any]) -> float:
        r = max(0.0, min(1.0, float(c.get("reward") or 0.0)))
        s2 = max(0.0, float(c.get("sigma") or 0.0)) ** 2
        m = max(0.0, min(1.0, float(c.get("lesson_match") or 0.0)))
        return r * math.exp(-2.0 * s2) * (1.0 + 0.35 * m)

    scored = sorted(((str(c["id"]), _amp(c)) for c in cands), key=lambda x: x[1], reverse=True)
    masses = [abs(a) ** 2 for _, a in scored]
    mx = max(masses) if masses else 1.0
    ex = [math.exp((x - mx) / 0.65) for x in masses]
    z = sum(ex) or 1e-15
    order = [i for i, _ in scored]
    return {
        "engine": "python_fallback",
        "order": order,
        "best_id": order[0] if order else None,
        "amplitudes": [[i, a] for i, a in scored],
        "born_probs": [[i, e / z] for (i, _), e in zip(scored, ex, strict=True)],
        "sha12": SHA12,
        "schema": "unigrok.v1.door.v1",
        "promote": "NO",
        "authority": "ADVICE",
    }


def _tool_names(tools: Any) -> str:
    names: list[str] = []
    if isinstance(tools, list):
        for item in tools[:24]:
            if isinstance(item, str) and item.strip():
                names.append(item.strip()[:40])
                continue
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else item
            name = str((fn or {}).get("name") or item.get("name") or "").strip()
            if name:
                names.append(name[:40])
    return ",".join(names)[:240]


def _small(val: Any) -> bool:
    if isinstance(val, (int, float, bool)):
        return True
    if isinstance(val, str):
        return len(val) <= 80
    return False


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content)


def _result_text(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result or "")
    text = result.get("text")
    if isinstance(text, str):
        return text
    return ""


def _result_usage(result: Any) -> dict[str, int]:
    if not isinstance(result, dict):
        return _usage(None)
    prompt_n = int(result.get("input_tokens") or result.get("prompt_tokens") or 0)
    out_n = int(result.get("output_tokens") or result.get("completion_tokens") or 0)
    total = int(result.get("total_tokens") or (prompt_n + out_n))
    return _usage(
        {"prompt_tokens": prompt_n, "completion_tokens": out_n, "total_tokens": total}
    )


def _usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_n = int(usage.get("prompt_tokens") or 0)
    out_n = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt_n + out_n))
    return {
        "prompt_tokens": prompt_n,
        "completion_tokens": out_n,
        "total_tokens": total,
    }


def _chunk_text(text: str) -> list[str]:
    if not text:
        return []
    return [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)]


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]
