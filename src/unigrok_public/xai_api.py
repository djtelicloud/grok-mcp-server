from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

API_PLANE = "xai_api_key"
MANAGEMENT_KEY_CANARY = "xai-management-api-disabled-in-public-core"
DEFAULT_API_MODEL = os.environ.get("UNIGROK_API_MODEL", "").strip() or None
API_TIMEOUT_SECONDS = max(
    30,
    min(int(os.environ.get("UNIGROK_API_TIMEOUT", "120") or 120), 600),
)


def api_key_configured() -> bool:
    return bool(os.environ.get("XAI_API_KEY", "").strip())


def _require_key() -> str:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "The xAI API plane is not configured. Set XAI_API_KEY in the server environment; "
            "never place it in an IDE MCP configuration or send it through chat."
        )
    return key


def _client() -> Any:
    from xai_sdk import Client

    return Client(api_key=_require_key(), management_api_key=MANAGEMENT_KEY_CANARY)


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


async def _blocking(call: Callable[[], Any], deadline_seconds: float | None = None) -> Any:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(call), timeout=deadline_seconds or API_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        raise RuntimeError("The xAI API request timed out") from exc


def _model_name(model: Any) -> str:
    return str(getattr(model, "name", None) or getattr(model, "id", None) or model).strip()


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _chat_result(response: Any, *, model: str, route: str, started: float) -> dict[str, Any]:
    citations = [str(url) for url in (getattr(response, "citations", None) or [])]
    outputs: list[str] = []
    for output in getattr(response, "tool_outputs", None) or []:
        message = getattr(output, "message", None)
        content = getattr(message, "content", None)
        if content:
            outputs.append(str(content))
    return {
        "text": str(getattr(response, "content", "") or ""),
        "model": str(getattr(response, "model", None) or model),
        "stop_reason": str(getattr(response, "finish_reason", None) or "unknown"),
        "response_id": getattr(response, "id", None),
        "plane": API_PLANE,
        "billing_class": "metered_api",
        "workspace_attached": False,
        "cost_usd": float(getattr(response, "cost_usd", 0.0) or 0.0),
        "usage": _usage(response),
        "citations": citations,
        "tool_outputs": outputs,
        "route": route,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


async def probe_models() -> dict[str, Any]:
    if not api_key_configured():
        return {
            "ready": False,
            "configured": False,
            "authenticated": False,
            "models": [],
            "default_model": DEFAULT_API_MODEL,
        }

    def _entries(items: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for model in items:
            name = _model_name(model)
            if not name:
                continue
            entry: dict[str, Any] = {"id": name}
            context = getattr(model, "max_prompt_length", None)
            if context:
                entry["context_window"] = int(context)
            entries.append(entry)
        return sorted(entries, key=lambda item: item["id"])

    def _call() -> dict[str, list[dict[str, Any]]]:
        client = _client()
        try:
            return {
                "language_models": _entries(client.models.list_language_models()),
                "image_models": _entries(client.models.list_image_generation_models()),
            }
        finally:
            _close_client(client)

    try:
        catalogs = await _blocking(_call, deadline_seconds=15)
    except Exception:
        return {
            "ready": False,
            "configured": True,
            "authenticated": False,
            "models": [],
            "default_model": DEFAULT_API_MODEL,
            "reason": "model discovery failed",
        }
    models = catalogs["language_models"]
    image_models = catalogs["image_models"]
    ids = [entry["id"] for entry in models]
    default = DEFAULT_API_MODEL if DEFAULT_API_MODEL in ids else (ids[0] if ids else None)
    return {
        "ready": bool(ids),
        "configured": True,
        "authenticated": bool(ids),
        "models": models,
        "language_models": models,
        "image_models": image_models,
        "default_model": default,
    }


async def resolve_model(requested: str | None) -> str:
    status = await probe_models()
    if not status["ready"]:
        raise RuntimeError("The xAI API plane is not ready")
    ids = [entry["id"] for entry in status["models"]]
    if requested:
        if requested not in ids:
            raise ValueError(f"model '{requested}' is not available on the xAI API plane")
        return requested
    default = status.get("default_model")
    if not default:
        raise RuntimeError("The xAI API returned no language models")
    return str(default)


async def chat(
    prompt: str,
    *,
    model: str | None,
    reasoning_effort: str | None,
    system_prompt: str,
    allow_web: bool = False,
    allow_x_search: bool = False,
    allow_code: bool = False,
    max_turns: int | None = None,
    max_tokens: int | None = None,
    response_format: str | None = None,
) -> dict[str, Any]:
    selected = await resolve_model(model)
    started = time.monotonic()

    def _call() -> Any:
        from xai_sdk.chat import system, user
        from xai_sdk.tools import code_execution, web_search, x_search

        tools = []
        if allow_web:
            tools.append(web_search())
        if allow_x_search:
            tools.append(x_search())
        if allow_code:
            tools.append(code_execution())
        params: dict[str, Any] = {"model": selected}
        if tools:
            params["tools"] = tools
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
        if max_turns is not None:
            params["max_turns"] = max_turns
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if response_format is not None:
            params["response_format"] = response_format
        client = _client()
        try:
            conversation = client.chat.create(**params)
            conversation.append(system(system_prompt))
            conversation.append(user(prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    response = await _blocking(_call)
    route = "agent" if any((allow_web, allow_x_search, allow_code)) else "chat"
    return _chat_result(response, model=selected, route=route, started=started)


async def vision(
    prompt: str,
    image_urls: Sequence[str],
    *,
    model: str | None,
    detail: str,
    system_prompt: str,
) -> dict[str, Any]:
    selected = await resolve_model(model)
    started = time.monotonic()

    def _call() -> Any:
        from xai_sdk.chat import image, system, user

        client = _client()
        try:
            conversation = client.chat.create(model=selected)
            conversation.append(system(system_prompt))
            content = [image(image_url=url, detail=detail) for url in image_urls]
            conversation.append(user(*content, prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(await _blocking(_call), model=selected, route="vision", started=started)


async def chat_files(
    prompt: str,
    file_ids: Sequence[str],
    *,
    model: str | None,
    system_prompt: str,
) -> dict[str, Any]:
    selected = await resolve_model(model)
    started = time.monotonic()

    def _call() -> Any:
        from xai_sdk.chat import file as xai_file
        from xai_sdk.chat import system, user

        client = _client()
        try:
            conversation = client.chat.create(model=selected)
            conversation.append(system(system_prompt))
            conversation.append(user(prompt, *(xai_file(file_id) for file_id in file_ids)))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(await _blocking(_call), model=selected, route="files", started=started)


async def search(
    prompt: str,
    *,
    kind: str,
    model: str | None,
    system_prompt: str,
    allowed_domains: Sequence[str] | None = None,
    excluded_domains: Sequence[str] | None = None,
    allowed_x_handles: Sequence[str] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    selected = await resolve_model(model)
    started = time.monotonic()

    def _call() -> Any:
        from xai_sdk.chat import system, user
        from xai_sdk.tools import web_search, x_search

        if kind == "web":
            tool = web_search(
                allowed_domains=list(allowed_domains) or None,
                excluded_domains=list(excluded_domains) or None,
            )
        elif kind == "x":
            tool = x_search(
                allowed_x_handles=list(allowed_x_handles) or None,
                from_date=datetime.fromisoformat(from_date) if from_date else None,
                to_date=datetime.fromisoformat(to_date) if to_date else None,
            )
        else:
            raise ValueError("unsupported search kind")
        client = _client()
        try:
            conversation = client.chat.create(model=selected, tools=[tool])
            conversation.append(system(system_prompt))
            conversation.append(user(prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(
        await _blocking(_call), model=selected, route=f"{kind}_search", started=started
    )


async def code_execution(
    prompt: str,
    *,
    model: str | None,
    max_turns: int,
    system_prompt: str,
) -> dict[str, Any]:
    selected = await resolve_model(model)
    started = time.monotonic()

    def _call() -> Any:
        from xai_sdk.chat import system, user
        from xai_sdk.tools import code_execution as code_tool

        client = _client()
        try:
            conversation = client.chat.create(
                model=selected,
                tools=[code_tool()],
                include=["code_execution_call_output"],
                max_turns=max_turns,
            )
            conversation.append(system(system_prompt))
            conversation.append(user(prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(
        await _blocking(_call), model=selected, route="code_execution", started=started
    )


def _media_result(item: Any) -> dict[str, Any]:
    return {
        "url": getattr(item, "url", None),
        "revised_prompt": getattr(item, "prompt", None),
        "duration_seconds": getattr(item, "duration", None),
        "cost_usd": float(getattr(item, "cost_usd", 0.0) or 0.0),
    }


async def generate_image(
    prompt: str,
    *,
    model: str,
    image_urls: Sequence[str],
    n: int,
    aspect_ratio: str | None,
    resolution: str | None,
) -> dict[str, Any]:
    started = time.monotonic()

    def _call() -> Any:
        params: dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "n": n,
            "image_format": "url",
        }
        if image_urls:
            params["image_urls"] = list(image_urls)
        if aspect_ratio:
            params["aspect_ratio"] = aspect_ratio
        if resolution:
            params["resolution"] = resolution
        client = _client()
        try:
            return client.image.sample_batch(**params)
        finally:
            _close_client(client)

    images = [_media_result(item) for item in await _blocking(_call)]
    return {
        "images": images,
        "model": model,
        "plane": API_PLANE,
        "billing_class": "metered_api",
        "cost_usd": sum(item["cost_usd"] for item in images),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


async def generate_video(
    prompt: str,
    *,
    model: str,
    image_url: str | None,
    video_url: str | None,
    reference_image_urls: Sequence[str],
    duration: int | None,
    aspect_ratio: str | None,
    resolution: str | None,
) -> dict[str, Any]:
    started = time.monotonic()

    def _call() -> Any:
        params: dict[str, Any] = {"prompt": prompt, "model": model}
        for key, value in (
            ("image_url", image_url),
            ("video_url", video_url),
            ("duration", duration),
            ("aspect_ratio", aspect_ratio),
            ("resolution", resolution),
        ):
            if value is not None:
                params[key] = value
        if reference_image_urls:
            params["reference_image_urls"] = list(reference_image_urls)
        client = _client()
        try:
            return client.video.generate(**params)
        finally:
            _close_client(client)

    result = _media_result(await _blocking(_call, deadline_seconds=300))
    return {
        "video": result,
        "model": model,
        "plane": API_PLANE,
        "billing_class": "metered_api",
        "cost_usd": result["cost_usd"],
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


async def extend_video(
    prompt: str,
    *,
    model: str,
    video_url: str,
    duration: int | None,
) -> dict[str, Any]:
    started = time.monotonic()

    def _call() -> Any:
        params: dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "video_url": video_url,
        }
        if duration is not None:
            params["duration"] = duration
        client = _client()
        try:
            return client.video.extend(**params)
        finally:
            _close_client(client)

    result = _media_result(await _blocking(_call, deadline_seconds=300))
    return {
        "video": result,
        "model": model,
        "plane": API_PLANE,
        "billing_class": "metered_api",
        "cost_usd": result["cost_usd"],
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def _file_metadata(item: Any) -> dict[str, Any]:
    return {
        "file_id": str(getattr(item, "id", "") or ""),
        "filename": str(getattr(item, "filename", "") or ""),
        "size_bytes": int(getattr(item, "size", None) or getattr(item, "bytes", 0) or 0),
        "public_url": getattr(item, "public_url", None),
        "expires_at": str(getattr(item, "expires_at", "") or "") or None,
    }


async def upload_file(
    content: bytes, *, filename: str, expires_after_seconds: int
) -> dict[str, Any]:
    def _call() -> Any:
        client = _client()
        try:
            return client.files.upload(
                content,
                filename=filename,
                expires_after=expires_after_seconds,
            )
        finally:
            _close_client(client)

    result = _file_metadata(await _blocking(_call, deadline_seconds=60))
    result.update({"plane": API_PLANE, "billing_class": "metered_api"})
    return result


async def list_files(limit: int) -> dict[str, Any]:
    def _call() -> Any:
        client = _client()
        try:
            return client.files.list(limit=limit)
        finally:
            _close_client(client)

    response = await _blocking(_call, deadline_seconds=30)
    data = getattr(response, "data", response) or []
    return {"files": [_file_metadata(item) for item in data], "plane": API_PLANE}


async def get_file(file_id: str) -> dict[str, Any]:
    def _call() -> Any:
        client = _client()
        try:
            return client.files.get(file_id)
        finally:
            _close_client(client)

    result = _file_metadata(await _blocking(_call, deadline_seconds=30))
    result["plane"] = API_PLANE
    return result


async def get_file_content(file_id: str, *, max_bytes: int) -> dict[str, Any]:
    def _call() -> bytes:
        client = _client()
        try:
            return bytes(client.files.content(file_id))
        finally:
            _close_client(client)

    content = await _blocking(_call, deadline_seconds=30)
    shown = content[:max_bytes]
    try:
        text = shown.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        import base64

        text = base64.b64encode(shown).decode("ascii")
        encoding = "base64"
    return {
        "content": text,
        "encoding": encoding,
        "bytes_returned": len(shown),
        "total_bytes": len(content),
        "truncated": len(shown) < len(content),
        "plane": API_PLANE,
    }


async def delete_file(file_id: str) -> dict[str, Any]:
    def _call() -> None:
        client = _client()
        try:
            client.files.delete(file_id)
        finally:
            _close_client(client)

    await _blocking(_call, deadline_seconds=30)
    return {"deleted": True, "file_id": file_id, "plane": API_PLANE}
