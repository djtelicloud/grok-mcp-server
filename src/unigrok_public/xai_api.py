from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from .principal_xai import (
    PrincipalXAIConfigurationError,
    active_credential_source,
    resolve_inference_credential,
)

API_PLANE = "xai_api_key"
MANAGEMENT_KEY_CANARY = "xai-management-api-disabled-in-public-core"
DEFAULT_API_MODEL = os.environ.get("UNIGROK_API_MODEL", "").strip() or None
API_TIMEOUT_SECONDS = max(
    30,
    min(int(os.environ.get("UNIGROK_API_TIMEOUT", "120") or 120), 600),
)
# Split concurrency pools so slow files.list (up to ~120s) cannot HOL-block
# metered generation (B1). Generation keeps the historic inflight cap; file
# reads get a smaller dedicated pool.
API_MAX_INFLIGHT = max(1, min(int(os.environ.get("UNIGROK_API_MAX_INFLIGHT", "4") or 4), 16))
API_MAX_FILE_INFLIGHT = max(
    1, min(int(os.environ.get("UNIGROK_API_MAX_FILE_INFLIGHT", "2") or 2), 4)
)
_API_GENERATION_WORKERS = asyncio.Semaphore(API_MAX_INFLIGHT)
_API_FILE_WORKERS = asyncio.Semaphore(API_MAX_FILE_INFLIGHT)
# Back-compat alias — historically one shared pool; now generation-only.
_API_WORKERS = _API_GENERATION_WORKERS
# files.list is observed ~40s cold; keep headroom under MCP poll loops.
FILE_LIST_TIMEOUT_SECONDS = max(
    60,
    min(int(os.environ.get("UNIGROK_FILE_LIST_TIMEOUT", "120") or 120), 600),
)
FILE_IO_TIMEOUT_SECONDS = max(
    30,
    min(int(os.environ.get("UNIGROK_FILE_IO_TIMEOUT", "60") or 60), 600),
)
MEDIA_TIMEOUT_SECONDS = max(
    60,
    min(int(os.environ.get("UNIGROK_MEDIA_TIMEOUT", "300") or 300), 600),
)
# Refuse to materialize files larger than this even when the caller asks for a
# smaller max_bytes window (SDK content() may not stream).
FILE_CONTENT_HARD_CAP_BYTES = max(
    1_024,
    min(int(os.environ.get("UNIGROK_FILE_CONTENT_MAX_BYTES", "2000000") or 2_000_000), 10_000_000),
)


def api_key_configured() -> bool:
    try:
        key, _source, _generation = resolve_inference_credential()
    except PrincipalXAIConfigurationError:
        return False
    return bool(key)


def _require_key() -> str:
    try:
        key, _source, _generation = resolve_inference_credential()
    except PrincipalXAIConfigurationError as exc:
        raise RuntimeError("The principal xAI credential map is invalid") from exc
    if not key:
        raise RuntimeError(
            "The xAI API plane is not configured. Set XAI_API_KEY in the server environment; "
            "never place it in an IDE MCP configuration or send it through chat."
        )
    return key


def credential_cache_key() -> str:
    """Return a process-local, non-secret credential generation identifier."""
    try:
        _key, source, generation = resolve_inference_credential()
    except PrincipalXAIConfigurationError:
        return "configuration_error"
    return f"{source}:{generation}"


def _rpc_timeout(deadline_seconds: float) -> float:
    """Native gRPC deadline slightly under the await deadline.

    asyncio.wait_for alone abandons the awaiter while the worker thread keeps
    running against the SDK's 27-minute default. The Client timeout cancels the
    RPC so the thread can exit; keep it 2s under the await budget so gRPC loses
    the race cleanly.
    """
    return max(1.0, float(deadline_seconds) - 2.0)


def _client(*, timeout_seconds: float | None = None) -> Any:
    from xai_sdk import Client

    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(API_TIMEOUT_SECONDS)
    )
    return Client(
        api_key=_require_key(),
        management_api_key=MANAGEMENT_KEY_CANARY,
        timeout=max(1.0, timeout),
    )


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


async def _blocking(call: Callable[[], Any], deadline_seconds: float | None = None) -> Any:
    """Metered generation / mutations — uses the generation concurrency pool."""
    deadline = float(deadline_seconds or API_TIMEOUT_SECONDS)
    async with _API_GENERATION_WORKERS:
        try:
            return await asyncio.wait_for(asyncio.to_thread(call), timeout=deadline)
        except TimeoutError as exc:
            raise RuntimeError(
                f"The xAI API request timed out after {deadline:g}s"
            ) from exc


# Retry policy for IDEMPOTENT READS ONLY (list/get/probe/resolve). Reads carry no
# side effects and cannot double-spend, so a bounded retry lets transient network
# faults self-heal. Mutations (upload/delete) and metered generation
# (chat/image/video/search/code/vision) NEVER retry here — that would risk
# duplicate side effects or double billing.
# NOTE: retries only help CONNECTION faults. Timeouts mean the endpoint is slower
# than the deadline; retrying only multiplies wait and thread occupancy.
_READ_RETRY_BACKOFFS = (0.5, 1.5)  # seconds slept before retries (connection faults only)


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if type(exc).__name__ in {"DeadlineExceeded", "DeadlineExceededError"}:
        return True
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            return str(code()).rsplit(".", 1)[-1] == "DEADLINE_EXCEEDED"
        except Exception:
            return False
    return False


def _is_retryable(exc: BaseException) -> bool:
    # Never retry timeouts — they are deadline/policy failures, not blips.
    if _is_timeout(exc):
        return False
    # OSError covers ConnectionError/BrokenPipe/Reset, ssl.SSLError, and
    # ECONNRESET/ECONNREFUSED/ETIMEDOUT/EPIPE (but not TimeoutError — excluded above).
    if isinstance(exc, OSError):
        return True
    # httpx-style transport transients (matched by name to avoid a hard import).
    if type(exc).__name__ in {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
        "TransportError",
    }:
        return True
    # gRPC: only UNAVAILABLE / RESOURCE_EXHAUSTED (DEADLINE_EXCEEDED handled above).
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            if str(code()).rsplit(".", 1)[-1] in {"UNAVAILABLE", "RESOURCE_EXHAUSTED"}:
                return True
        except Exception:
            return False
    # HTTP status errors: retry only 429/502/503/504, never other 4xx.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {429, 502, 503, 504}


async def _blocking_read(call: Callable[[], Any], deadline_seconds: float | None = None) -> Any:
    """Idempotent file/catalog reads — dedicated pool, never blocks generation."""
    deadline = float(deadline_seconds or API_TIMEOUT_SECONDS)
    last: BaseException | None = None
    attempts = 0
    for attempt in range(len(_READ_RETRY_BACKOFFS) + 1):
        attempts = attempt + 1
        # Acquire per attempt; release before backoff sleep (B2).
        async with _API_FILE_WORKERS:
            try:
                return await asyncio.wait_for(asyncio.to_thread(call), timeout=deadline)
            except Exception as exc:  # noqa: BLE001 — re-raised below unless retryable
                if _is_timeout(exc):
                    raise RuntimeError(
                        f"The xAI API read timed out after {deadline:g}s "
                        "(native SDK deadline should have cancelled the RPC)"
                    ) from exc
                if not _is_retryable(exc):
                    raise
                last = exc
        if attempt < len(_READ_RETRY_BACKOFFS):
            await asyncio.sleep(_READ_RETRY_BACKOFFS[attempt])
    detail = str(last).strip() or type(last).__name__
    raise RuntimeError(
        f"The xAI API read failed after {attempts} attempts ({detail})"
    ) from last


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
        "credential_source": active_credential_source(),
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

    deadline = 15.0

    def _call() -> dict[str, list[dict[str, Any]]]:
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return {
                "language_models": _entries(client.models.list_language_models()),
                "image_models": _entries(client.models.list_image_generation_models()),
            }
        finally:
            _close_client(client)

    try:
        catalogs = await _blocking_read(_call, deadline_seconds=deadline)
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
    deadline = float(API_TIMEOUT_SECONDS)

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
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            conversation = client.chat.create(**params)
            conversation.append(system(system_prompt))
            conversation.append(user(prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    response = await _blocking(_call, deadline_seconds=deadline)
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
    deadline = float(API_TIMEOUT_SECONDS)

    def _call() -> Any:
        from xai_sdk.chat import image, system, user

        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            conversation = client.chat.create(model=selected)
            conversation.append(system(system_prompt))
            content = [image(image_url=url, detail=detail) for url in image_urls]
            conversation.append(user(*content, prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(
        await _blocking(_call, deadline_seconds=deadline),
        model=selected,
        route="vision",
        started=started,
    )


async def chat_files(
    prompt: str,
    file_ids: Sequence[str],
    *,
    model: str | None,
    system_prompt: str,
) -> dict[str, Any]:
    selected = await resolve_model(model)
    started = time.monotonic()
    deadline = float(API_TIMEOUT_SECONDS)

    def _call() -> Any:
        from xai_sdk.chat import file as xai_file
        from xai_sdk.chat import system, user

        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            conversation = client.chat.create(model=selected)
            conversation.append(system(system_prompt))
            conversation.append(user(prompt, *(xai_file(file_id) for file_id in file_ids)))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(
        await _blocking(_call, deadline_seconds=deadline),
        model=selected,
        route="files",
        started=started,
    )


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
    deadline = float(API_TIMEOUT_SECONDS)

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
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            conversation = client.chat.create(model=selected, tools=[tool])
            conversation.append(system(system_prompt))
            conversation.append(user(prompt))
            return conversation.sample()
        finally:
            _close_client(client)

    return _chat_result(
        await _blocking(_call, deadline_seconds=deadline),
        model=selected,
        route=f"{kind}_search",
        started=started,
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
    deadline = float(API_TIMEOUT_SECONDS)

    def _call() -> Any:
        from xai_sdk.chat import system, user
        from xai_sdk.tools import code_execution as code_tool

        client = _client(timeout_seconds=_rpc_timeout(deadline))
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
        await _blocking(_call, deadline_seconds=deadline),
        model=selected,
        route="code_execution",
        started=started,
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
    deadline = float(API_TIMEOUT_SECONDS)

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
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return client.image.sample_batch(**params)
        finally:
            _close_client(client)

    images = [
        _media_result(item)
        for item in await _blocking(_call, deadline_seconds=deadline)
    ]
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
    deadline = float(MEDIA_TIMEOUT_SECONDS)

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
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return client.video.generate(**params)
        finally:
            _close_client(client)

    result = _media_result(await _blocking(_call, deadline_seconds=deadline))
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
    deadline = float(MEDIA_TIMEOUT_SECONDS)

    def _call() -> Any:
        params: dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "video_url": video_url,
        }
        if duration is not None:
            params["duration"] = duration
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return client.video.extend(**params)
        finally:
            _close_client(client)

    result = _media_result(await _blocking(_call, deadline_seconds=deadline))
    return {
        "video": result,
        "model": model,
        "plane": API_PLANE,
        "billing_class": "metered_api",
        "cost_usd": result["cost_usd"],
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def _format_expires_at(value: Any) -> str | None:
    if value is None or value == "":
        return None
    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        try:
            dt = to_datetime()
            if getattr(dt, "year", 0) > 1970:
                return dt.astimezone(UTC).isoformat()
        except Exception:  # noqa: S110 — best-effort protobuf timestamp decode
            return None
    seconds = getattr(value, "seconds", None)
    if seconds is not None:
        try:
            sec = int(seconds)
            if sec > 0:
                return datetime.fromtimestamp(sec, tz=UTC).isoformat()
        except Exception:  # noqa: S110 — best-effort seconds field decode
            return None
    text = str(value).strip()
    if not text:
        return None
    # Protobuf Timestamp str() looks like "seconds: 1784450992\nnanos: …"
    if text.startswith("seconds:"):
        try:
            sec = int(text.split("seconds:", 1)[1].splitlines()[0].strip())
            if sec > 0:
                return datetime.fromtimestamp(sec, tz=UTC).isoformat()
        except Exception:
            return None
    return text


def _file_metadata(item: Any) -> dict[str, Any]:
    raw_size = getattr(item, "size", None)
    if raw_size is None:
        raw_size = getattr(item, "bytes", None)
    try:
        size_bytes = int(raw_size) if raw_size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    return {
        "file_id": str(getattr(item, "id", "") or ""),
        "filename": str(getattr(item, "filename", "") or ""),
        "size_bytes": size_bytes,
        "public_url": getattr(item, "public_url", None),
        "expires_at": _format_expires_at(getattr(item, "expires_at", None)),
    }


async def upload_file(
    content: bytes, *, filename: str, expires_after_seconds: int
) -> dict[str, Any]:
    deadline = float(FILE_IO_TIMEOUT_SECONDS)

    def _call() -> Any:
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return client.files.upload(
                content,
                filename=filename,
                expires_after=expires_after_seconds,
            )
        finally:
            _close_client(client)

    result = _file_metadata(await _blocking(_call, deadline_seconds=deadline))
    result.update({"plane": API_PLANE, "billing_class": "metered_api"})
    return result


async def list_files(limit: int) -> dict[str, Any]:
    deadline = float(FILE_LIST_TIMEOUT_SECONDS)

    def _call() -> Any:
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return client.files.list(limit=limit)
        finally:
            _close_client(client)

    # Slow cold-start list (~40s); durable MCP jobs poll so IDE deadlines stay short.
    response = await _blocking_read(_call, deadline_seconds=deadline)
    data = getattr(response, "data", response) or []
    return {"files": [_file_metadata(item) for item in data], "plane": API_PLANE}


async def get_file(file_id: str) -> dict[str, Any]:
    deadline = float(FILE_IO_TIMEOUT_SECONDS)

    def _call() -> Any:
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            return client.files.get(file_id)
        finally:
            _close_client(client)

    result = _file_metadata(await _blocking_read(_call, deadline_seconds=deadline))
    result["plane"] = API_PLANE
    return result


def _read_file_bytes_bounded(raw: Any, *, limit: int) -> tuple[bytes, int | None]:
    """Return (shown_bytes, total_bytes_if_known).

    Prefer a streaming read when the SDK object exposes ``read`` so we never
    buffer more than ``limit + 1`` bytes. Otherwise fall back to ``bytes()`` and
    rely on the metadata hard-cap checked by ``get_file_content``.
    """
    reader = getattr(raw, "read", None)
    if callable(reader):
        chunk = reader(limit + 1)
        data = bytes(chunk)
        if len(data) > limit:
            return data[:limit], None
        return data, len(data)
    data = bytes(raw)
    return data[:limit], len(data)


async def get_file_content(file_id: str, *, max_bytes: int) -> dict[str, Any]:
    deadline = float(FILE_IO_TIMEOUT_SECONDS)
    limit = max(1, min(int(max_bytes), FILE_CONTENT_HARD_CAP_BYTES))
    # Refuse oversized objects before the SDK materializes them into memory.
    meta = await get_file(file_id)
    raw_size = meta.get("size_bytes")
    if raw_size is None:
        raise ValueError(
            "file size metadata is missing; refuse unbounded download "
            "(xAI SDK content() is not a true stream)"
        )
    size_bytes = int(raw_size)
    if size_bytes < 0:
        raise ValueError("file size metadata is negative; refuse invalid download")
    if size_bytes > FILE_CONTENT_HARD_CAP_BYTES:
        raise ValueError(
            f"file is {size_bytes} bytes; refuse to download more than "
            f"{FILE_CONTENT_HARD_CAP_BYTES} bytes (set UNIGROK_FILE_CONTENT_MAX_BYTES)"
        )
    if size_bytes == 0:
        return {
            "content": "",
            "encoding": "utf-8",
            "bytes_returned": 0,
            "total_bytes": 0,
            "truncated": False,
            "plane": API_PLANE,
        }

    def _call() -> bytes:
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            # Prefer stream read when available; many SDK builds still buffer fully.
            shown, _ = _read_file_bytes_bounded(
                client.files.content(file_id), limit=limit
            )
            return shown
        finally:
            _close_client(client)

    shown = await _blocking_read(_call, deadline_seconds=deadline)
    total_bytes = size_bytes
    truncated = size_bytes > len(shown)
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
        "total_bytes": total_bytes,
        "truncated": truncated,
        "plane": API_PLANE,
    }


async def delete_file(file_id: str) -> dict[str, Any]:
    deadline = float(FILE_IO_TIMEOUT_SECONDS)

    def _call() -> None:
        client = _client(timeout_seconds=_rpc_timeout(deadline))
        try:
            client.files.delete(file_id)
        finally:
            _close_client(client)

    await _blocking(_call, deadline_seconds=deadline)
    return {"deleted": True, "file_id": file_id, "plane": API_PLANE}
