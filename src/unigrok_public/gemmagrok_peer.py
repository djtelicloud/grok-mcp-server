"""Optional loopback-only MCP helper for an operator-owned local model runtime.

This server is intentionally separate from UniGrok's public ``@grok`` server.
It never receives Grok credentials, never selects a remote provider, and is not
registered as an automatic fallback. Operators start it explicitly after they
have staged an OpenAI-compatible model runtime on the same machine.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__

SERVICE_NAME = "GemmaGrok local helper"
MAX_PROMPT_CHARS = 20_000
MAX_SYSTEM_PROMPT_CHARS = 8_000
MAX_TOKENS = 2_048
_LOCAL_RUNTIME_HOSTS = {
    "localhost",
    "host.docker.internal",
    "gateway.docker.internal",
}
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)
_TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "gemmagrok-local:*",
    ],
    allowed_origins=[
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        "http://gemmagrok-local:*",
    ],
)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _runtime_url(value: str | None = None) -> str:
    raw = value if value is not None else os.environ.get("GEMMAGROK_RUNTIME_URL", "")
    raw = raw.strip().rstrip("/")
    if not raw:
        raise RuntimeError("GemmaGrok local runtime URL is not configured")

    parsed = urlsplit(raw)
    if parsed.scheme != "http":
        raise RuntimeError("GemmaGrok runtime must use local HTTP")
    if parsed.username or parsed.password:
        raise RuntimeError("GemmaGrok runtime URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError("GemmaGrok runtime URL must contain only a local origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("GemmaGrok runtime URL has an invalid port") from exc
    if port is None:
        raise RuntimeError("GemmaGrok runtime URL must include an explicit port")

    host = (parsed.hostname or "").lower()
    local = host in _LOCAL_RUNTIME_HOSTS
    if not local:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
    if not local:
        raise RuntimeError("GemmaGrok runtime must resolve through an explicit local host")
    return raw


def _valid_text(value: str, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds the {limit} character limit")
    return text


async def _runtime_request(
    method: Literal["GET", "POST"],
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> Any:
    async with httpx.AsyncClient(
        timeout=_bounded_int("GEMMAGROK_TIMEOUT_SECONDS", 120, 5, 600),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        request_kwargs: dict[str, Any] = {"json": payload} if payload is not None else {}
        response = await client.request(method, f"{_runtime_url()}{path}", **request_kwargs)
        response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("GemmaGrok local runtime returned invalid JSON") from exc


async def _served_models() -> list[str]:
    payload = await _runtime_request("GET", "/v1/models")
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("GemmaGrok local runtime returned an invalid model catalog")
    models = [
        str(row.get("id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    ]
    return list(dict.fromkeys(models))


async def _resolve_model() -> str:
    models = await _served_models()
    configured = os.environ.get("GEMMAGROK_MODEL_ID", "").strip()
    if configured:
        if configured not in models:
            raise RuntimeError("configured GemmaGrok model is not served by the local runtime")
        return configured
    if len(models) == 1:
        return models[0]
    raise RuntimeError("GemmaGrok requires one served model or an explicit local model id")


def _completion(payload: Any) -> tuple[str, str]:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("GemmaGrok local runtime returned an invalid completion")
    choice = choices[0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    text = content.strip() if isinstance(content, str) else ""
    if not text:
        raise RuntimeError("GemmaGrok local runtime returned an empty final completion")
    return text, str(choice.get("finish_reason") or "unknown")


mcp = FastMCP(
    SERVICE_NAME,
    instructions=(
        "Optional local GemmaGrok helper. Use chat for direct local-model answers. "
        "This separate server has no web, shell, file, credential, or remote-provider access "
        "and is never an automatic fallback for @grok."
    ),
    host=os.environ.get("GEMMAGROK_HOST", "127.0.0.1"),
    port=_bounded_int("PORT", 4777, 1, 65535),
    streamable_http_path="/mcp",
    stateless_http=False,
    json_response=False,
    transport_security=_TRANSPORT_SECURITY,
)
mcp._mcp_server.version = __version__


@mcp.tool(annotations=_READ_ONLY)
async def chat(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Ask the explicitly configured local model; remote fallback is impossible."""
    user_prompt = _valid_text(prompt, "prompt", MAX_PROMPT_CHARS)
    system = None
    if system_prompt is not None and str(system_prompt).strip():
        system = _valid_text(str(system_prompt), "system_prompt", MAX_SYSTEM_PROMPT_CHARS)
    token_limit = max(1, min(int(max_tokens), MAX_TOKENS))
    model = await _resolve_model()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})
    payload = await _runtime_request(
        "POST",
        "/v1/chat/completions",
        payload={
            "model": model,
            "messages": messages,
            "max_tokens": token_limit,
            "stream": False,
        },
    )
    text, finish_reason = _completion(payload)
    return {
        "text": text,
        "model": model,
        "source": "gemmagrok",
        "plane": "local",
        "degraded": True,
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
        "finish_reason": finish_reason,
        "remote_fallback": False,
    }


@mcp.tool(annotations=_READ_ONLY)
async def status() -> dict[str, Any]:
    """Return non-secret readiness for the optional local-only helper."""
    try:
        model = await _resolve_model()
    except Exception:
        return {
            "service": SERVICE_NAME,
            "runtime": "local",
            "ready": False,
            "model": None,
            "remote_fallback": False,
        }
    return {
        "service": SERVICE_NAME,
        "runtime": "local",
        "ready": True,
        "model": model,
        "remote_fallback": False,
    }


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": SERVICE_NAME})


@mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
async def readyz(_: Request) -> JSONResponse:
    result = await status()
    if not result["ready"]:
        return JSONResponse(
            {"status": "not_ready", "service": SERVICE_NAME},
            status_code=503,
        )
    return JSONResponse({"status": "ready", "service": SERVICE_NAME, "model": result["model"]})


def main() -> None:
    transport = os.environ.get("GEMMAGROK_TRANSPORT", "streamable-http").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise ValueError("GEMMAGROK_TRANSPORT must be 'stdio' or 'streamable-http'")

    import uvicorn

    uvicorn.run(mcp.streamable_http_app(), host=mcp.settings.host, port=mcp.settings.port)


if __name__ == "__main__":
    main()
