"""Optional loopback-only MCP helper for an operator-owned local model runtime.

This server is intentionally separate from UniGrok's public ``@grok`` server.
It never receives Grok credentials, never selects a remote provider, and is not
registered as an automatic fallback. Operators start it explicitly after they
have staged an OpenAI-compatible model runtime on the same machine.

Optional named-session notebooks live under ``GEMMAGROK_STATE_DIR`` (default
``/tmp/gemmagrok-sessions``). They never read gym boards or IDE state. Few-shot
exemplars are opt-in via ``icl_exemplars`` plus ``GEMMAGROK_EXEMPLAR_DIR``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
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
_SELF_CONF_FALLBACK_DEFAULT = 80
_SESSION_MAX_TURNS = 30
_SESSION_MAX_FACTS = 60
_ICL_DEFAULT_K = 0
_STORE_RE = re.compile(
    r"\bstore\s+([A-Za-z0-9_.\-]{1,64})\s*=\s*(\S{1,256})", re.IGNORECASE
)
_REDACT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|bearer|authorization)\b\s*[:=]\s*\S+"
)
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


def _redact(text: str) -> str:
    return _REDACT_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)


def _session_dir() -> Path:
    raw = os.environ.get("GEMMAGROK_STATE_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "gemmagrok-sessions"


def _safe_session_file(raw_id: str) -> Path:
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]", "", raw_id)[:80]
    if not cleaned or cleaned != raw_id:
        digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
        cleaned = f"{cleaned or 'sess'}-{digest}"
    return _session_dir() / f"{cleaned}.json"


def _resolve_session_id(
    session_id: str | None, session: str | None, memory_scope: str | None
) -> str | None:
    for candidate in (session_id, session, memory_scope):
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _load_session(raw_id: str) -> dict[str, Any]:
    path = _safe_session_file(raw_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("turns", [])
            data.setdefault("facts", [])
            return data
    except (OSError, ValueError):
        pass
    return {"session_id": raw_id, "turns": [], "facts": []}


def _save_session(raw_id: str, data: dict[str, Any]) -> None:
    path = _safe_session_file(raw_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["turns"] = data.get("turns", [])[-_SESSION_MAX_TURNS:]
    data["facts"] = data.get("facts", [])[-_SESSION_MAX_FACTS:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _session_preamble(data: dict[str, Any]) -> str:
    parts: list[str] = []
    facts = data.get("facts", [])
    if facts:
        lines = [
            f"- {f.get('key')}={f.get('value')}"
            for f in facts[-10:]
            if isinstance(f, dict)
        ]
        if lines:
            parts.append("Session facts (stored earlier in this session):\n" + "\n".join(lines))
    turns = data.get("turns", [])
    if turns:
        lines = []
        for turn in turns[-4:]:
            if isinstance(turn, dict):
                lines.append(f"user: {str(turn.get('prompt', ''))[:220]}")
                lines.append(f"assistant: {str(turn.get('reply', ''))[:220]}")
        if lines:
            parts.append("Recent session turns:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _declared_self_conf() -> int:
    raw = os.environ.get("GEMMAGROK_SELF_CONF", "").strip()
    if raw:
        try:
            return max(1, min(100, int(raw)))
        except ValueError:
            pass
    return _SELF_CONF_FALLBACK_DEFAULT


def _load_icl_exemplars(k: int) -> list[str]:
    if k <= 0:
        return []
    root = os.environ.get("GEMMAGROK_EXEMPLAR_DIR", "").strip()
    if not root:
        return []
    path = Path(root)
    try:
        files = sorted(path.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    bodies: list[str] = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            bodies.append(text)
        if len(bodies) >= k:
            break
    return bodies


mcp = FastMCP(
    SERVICE_NAME,
    instructions=(
        "Optional local GemmaGrok helper. Use chat for direct local-model answers. "
        "Name a session to keep a local notebook; anonymous calls stay stateless. "
        "This separate server has no web, shell, credential, or remote-provider access "
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
    session_id: str | None = None,
    session: str | None = None,
    memory_scope: str | None = None,
    icl_exemplars: int = _ICL_DEFAULT_K,
) -> dict[str, Any]:
    """Ask the explicitly configured local model; remote fallback is impossible.

    Naming a session loads that notebook before answering. Literal
    ``store KEY=VALUE`` in the caller prompt is captured as a session fact.
    Few-shot files from ``GEMMAGROK_EXEMPLAR_DIR`` are opt-in (default 0).
    """
    user_prompt = _valid_text(prompt, "prompt", MAX_PROMPT_CHARS)
    system = None
    if system_prompt is not None and str(system_prompt).strip():
        system = _valid_text(str(system_prompt), "system_prompt", MAX_SYSTEM_PROMPT_CHARS)
    token_limit = max(1, min(int(max_tokens), MAX_TOKENS))

    sid = _resolve_session_id(session_id, session, memory_scope)
    session_data = _load_session(sid) if sid else None

    preamble_parts: list[str] = []
    if session_data is not None:
        block = _session_preamble(session_data)
        if block:
            preamble_parts.append(block)
    exemplar_bodies = _load_icl_exemplars(max(0, min(int(icl_exemplars), 16)))
    base_chars = len(user_prompt) + (len(system) if system else 0) + sum(
        len(part) for part in preamble_parts
    )
    kept_exemplars: list[str] = []
    for body in exemplar_bodies:
        if base_chars + sum(len(item) for item in kept_exemplars) + len(body) > MAX_PROMPT_CHARS:
            break
        kept_exemplars.append(body)
    if kept_exemplars:
        preamble_parts.append(
            "Reference exemplars — operator-supplied local notes, newest first:\n\n"
            + "\n\n---\n\n".join(kept_exemplars)
        )

    model = await _resolve_model()
    messages: list[dict[str, str]] = []
    if preamble_parts:
        messages.append({"role": "system", "content": "\n\n".join(preamble_parts)})
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

    if sid and session_data is not None:
        session_data["turns"].append(
            {"prompt": _redact(user_prompt)[:2000], "reply": _redact(text)[:2000]}
        )
        for match in _STORE_RE.finditer(user_prompt):
            session_data["facts"].append(
                {"key": match.group(1), "value": _redact(match.group(2))}
            )
        try:
            _save_session(sid, session_data)
        except OSError:
            pass

    result: dict[str, Any] = {
        "text": text,
        "model": model,
        "source": "gemmagrok",
        "plane": "local",
        "degraded": True,
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
        "finish_reason": finish_reason,
        "remote_fallback": False,
        "self_conf": _declared_self_conf(),
        "icl_exemplars_used": len(kept_exemplars),
        "icl_exemplar_chars": sum(len(item) for item in kept_exemplars),
    }
    if sid:
        result["session_id"] = sid
    return result


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
