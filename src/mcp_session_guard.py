"""Inert ASGI guard for a future stateful MCP HTTP transport.

The public HTTP server does not install this module yet.  It exists so the
stateful activation can later be a small, reviewable wiring change instead of
mixing admission control, identity binding, expiry, and SDK-private cleanup in
one step.

The guard deliberately gets the authenticated principal from an injected
request-scope resolver.  It never reads request context variables: stateful
MCP server tasks may outlive the request whose context created them.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Protocol

import anyio
from mcp.types import InitializeRequest, InitializeResult
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send


MCP_SESSION_ID_HEADER = b"mcp-session-id"
MCP_SESSION_BINDING_SCOPE_KEY = "unigrok.mcp_session.binding"

_SDK_PRIVATE_INTERFACE_MINOR = (1, 28)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_CLIENT_ID_RE = re.compile(rb"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SINGLETON_SECURITY_HEADERS = frozenset(
    {
        b"authorization",
        b"host",
        b"mcp-protocol-version",
        MCP_SESSION_ID_HEADER,
        b"origin",
        b"x-client-id",
    }
)


class SessionTransportRegistry(Protocol):
    """Narrow transport ownership used by the guard.

    The production implementation below is the only place that knows the
    Python MCP SDK's private stateful-session map and creation lock.
    """

    async def contains(self, session_id: str) -> bool: ...

    async def session_ids(self) -> frozenset[str]: ...

    async def remove_and_terminate(self, session_id: str) -> bool: ...


class SessionRuntimeRevoker(Protocol):
    """Revokes all session-owned callback/effect authority before teardown."""

    async def revoke_session(self, session_id: str) -> None: ...


class MCP128SessionTransportRegistry:
    """Version-checked adapter over MCP SDK 1.28 private session state.

    This adapter is intentionally constructed with one exact session manager;
    it never discovers or mutates a module-global manager.  A future SDK minor
    must receive an explicit compatibility review before this adapter accepts
    it.
    """

    def __init__(self, manager: Any) -> None:
        sdk_version = metadata.version("mcp")
        try:
            major, minor = (int(part) for part in sdk_version.split(".", 2)[:2])
        except (TypeError, ValueError):
            raise RuntimeError("unrecognized MCP SDK version") from None
        if (major, minor) != _SDK_PRIVATE_INTERFACE_MINOR:
            raise RuntimeError(
                "MCP private session registry requires an explicit SDK minor review"
            )

        instances = getattr(manager, "_server_instances", None)
        owners = getattr(manager, "_session_owners", None)
        creation_lock = getattr(manager, "_session_creation_lock", None)
        if not isinstance(instances, MutableMapping):
            raise RuntimeError("MCP SDK session registry shape changed")
        if not isinstance(owners, MutableMapping):
            raise RuntimeError("MCP SDK session owner registry shape changed")
        if not callable(getattr(creation_lock, "__aenter__", None)) or not callable(
            getattr(creation_lock, "__aexit__", None)
        ):
            raise RuntimeError("MCP SDK session creation lock shape changed")
        self._instances = instances
        self._owners = owners
        self._creation_lock = creation_lock

    async def contains(self, session_id: str) -> bool:
        async with self._creation_lock:
            return session_id in self._instances

    async def session_ids(self) -> frozenset[str]:
        async with self._creation_lock:
            return frozenset(self._instances)

    async def remove_and_terminate(self, session_id: str) -> bool:
        owner_missing = object()
        async with self._creation_lock:
            transport = self._instances.pop(session_id, None)
            owner = self._owners.pop(session_id, owner_missing)
            if transport is None:
                return False
        try:
            terminate = getattr(transport, "terminate", None)
            if not callable(terminate):
                raise RuntimeError("MCP SDK transport termination interface changed")
            await terminate()
        except BaseException:
            async with self._creation_lock:
                if session_id not in self._instances:
                    self._instances[session_id] = transport
                    if owner is not owner_missing:
                        self._owners[session_id] = owner
            raise
        return True


@dataclass(frozen=True, slots=True)
class MCPSessionBinding:
    """Immutable server-owned identity for one physical MCP session."""

    session_id: str
    principal: str = field(repr=False)
    client_id: bytes | None = field(repr=False)
    created_monotonic: float


@dataclass(slots=True)
class _SessionCleanupOutcome:
    error: BaseException | None = None


@dataclass(slots=True)
class _SessionRecord:
    binding: MCPSessionBinding
    last_activity_monotonic: float
    active_calls: int = 0
    active_scopes: set[anyio.CancelScope] = field(default_factory=set)
    closing: bool = False
    cleanup_retry_required: bool = False
    cleanup_task: asyncio.Task[_SessionCleanupOutcome] | None = None


@dataclass(slots=True)
class _PendingTransportCleanup:
    task: asyncio.Task[bool] | None = None


class _DuplicateJSONObject(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONObject
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-RFC JSON constant: {value}")


def _load_strict_json(payload: bytes | str) -> Any:
    return json.loads(
        payload,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )


def _parse_exact_initialize(body: bytes) -> str | int | None:
    try:
        message = _load_strict_json(body)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(message, dict) or set(message) != {
        "jsonrpc",
        "id",
        "method",
        "params",
    }:
        return None
    request_id = message["id"]
    if (
        message["jsonrpc"] != "2.0"
        or message["method"] != "initialize"
        or isinstance(request_id, bool)
        or not isinstance(request_id, (str, int))
        or not isinstance(message["params"], dict)
    ):
        return None
    try:
        parsed = InitializeRequest.model_validate(
            {"method": "initialize", "params": message["params"]},
            strict=True,
        )
    except ValidationError:
        return None
    client_info = parsed.params.clientInfo
    if (
        not client_info.name.strip()
        or not client_info.version.strip()
        or any(
            ord(char) < 32 or ord(char) == 127
            for char in client_info.name + client_info.version
        )
    ):
        return None
    return request_id


def _response_json_values(
    *, content_type: bytes | None, body: bytes
) -> tuple[Any, ...]:
    try:
        if content_type is not None and content_type.split(b";", 1)[0].strip().lower() == b"text/event-stream":
            text = body.decode("utf-8", errors="strict").replace("\r\n", "\n")
            values: list[Any] = []
            for event in text.split("\n\n"):
                data_lines = [
                    line[5:].lstrip(" ")
                    for line in event.splitlines()
                    if line.startswith("data:")
                ]
                if data_lines:
                    values.append(_load_strict_json("\n".join(data_lines)))
            return tuple(values)
        return (_load_strict_json(body),)
    except (UnicodeDecodeError, ValueError):
        return ()


def _is_verified_initialize_response(
    *,
    request_id: str | int,
    content_type: bytes | None,
    body: bytes,
) -> bool:
    matching: list[Mapping[str, Any]] = []
    for value in _response_json_values(content_type=content_type, body=body):
        if not isinstance(value, Mapping):
            continue
        response_id = value.get("id")
        if (
            isinstance(response_id, bool)
            or type(response_id) is not type(request_id)
            or response_id != request_id
        ):
            continue
        matching.append(value)
    if len(matching) != 1:
        return False
    response = matching[0]
    if (
        response.get("jsonrpc") != "2.0"
        or "error" in response
        or set(response) != {"jsonrpc", "id", "result"}
        or not isinstance(response.get("result"), Mapping)
    ):
        return False
    try:
        InitializeResult.model_validate(response["result"], strict=True)
    except ValidationError:
        return False
    return True


def _header_values(scope: Mapping[str, Any], name: bytes) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for raw_name, raw_value in scope.get("headers") or ():
        try:
            normalized = raw_name.lower()
        except AttributeError:
            continue
        if normalized == name and isinstance(raw_value, bytes):
            values.append(raw_value)
    return tuple(values)


def _has_duplicate_security_header(scope: Mapping[str, Any]) -> bool:
    counts: dict[bytes, int] = {}
    for raw_name, _ in scope.get("headers") or ():
        if not isinstance(raw_name, bytes):
            continue
        name = raw_name.lower()
        if name not in _SINGLETON_SECURITY_HEADERS:
            continue
        counts[name] = counts.get(name, 0) + 1
        if counts[name] > 1:
            return True
    return False


def _valid_principal(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 240
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _valid_client_id(value: bytes | None) -> bool:
    return value is None or _CLIENT_ID_RE.fullmatch(value) is not None


def _parse_session_id(value: bytes) -> str | None:
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        return None
    return decoded if _SESSION_ID_RE.fullmatch(decoded) else None


async def _send_fixed_response(
    send: Send,
    *,
    status: int,
    body: bytes,
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _read_request_body(receive: Receive, *, limit: int) -> bytes | None:
    chunks: list[bytes] = []
    length = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        if message["type"] != "http.request":
            return None
        chunk = message.get("body", b"")
        if not isinstance(chunk, bytes):
            return None
        length += len(chunk)
        if length > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay_body(body: bytes, original_receive: Receive) -> Receive:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Stateful SSE transports keep receiving after the request body to
        # observe the *real* client disconnect.  Inventing a disconnect here
        # would cancel the initialize response before its first event.
        return await original_receive()

    return receive


class StatefulMCPSessionGuard:
    """Admission, binding, expiry, and teardown for stateful MCP HTTP.

    Constructing the guard has no network, provider, credential, or transport
    effect.  The current UniGrok HTTP server deliberately does not install it.
    """

    _INVALID_REQUEST = b'{"error":"invalid_request"}'
    _SESSION_NOT_FOUND = b'{"error":"session_not_found"}'
    _CAPACITY_EXHAUSTED = b'{"error":"session_capacity_exhausted"}'
    _UNAVAILABLE = b'{"error":"session_guard_unavailable"}'

    def __init__(
        self,
        app: ASGIApp,
        *,
        registry: SessionTransportRegistry,
        runtime_revoker: SessionRuntimeRevoker,
        principal_resolver: Callable[[Scope], str],
        max_sessions: int = 64,
        idle_ttl_seconds: float = 15 * 60,
        max_ttl_seconds: float = 8 * 60 * 60,
        max_initialize_body_bytes: int = 1024 * 1024,
        max_initialize_response_bytes: int = 1024 * 1024,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(max_sessions, bool)
            or not isinstance(max_sessions, int)
            or max_sessions <= 0
        ):
            raise ValueError("max_sessions must be a positive integer")
        for name, value in (
            ("idle_ttl_seconds", idle_ttl_seconds),
            ("max_ttl_seconds", max_ttl_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if max_ttl_seconds < idle_ttl_seconds:
            raise ValueError("max_ttl_seconds cannot be shorter than idle TTL")
        for name, value in (
            ("max_initialize_body_bytes", max_initialize_body_bytes),
            ("max_initialize_response_bytes", max_initialize_response_bytes),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")

        self._app = app
        self._registry = registry
        self._runtime_revoker = runtime_revoker
        self._principal_resolver = principal_resolver
        self._max_sessions = max_sessions
        self._idle_ttl_seconds = float(idle_ttl_seconds)
        self._max_ttl_seconds = float(max_ttl_seconds)
        self._max_initialize_body_bytes = max_initialize_body_bytes
        self._max_initialize_response_bytes = max_initialize_response_bytes
        self._monotonic = monotonic
        self._lock = anyio.Lock()
        # Serializing admission lets a failed SDK initialize be associated
        # with, and clean up, only the transports created by that initialize.
        self._initialize_lock = anyio.Lock()
        self._sessions: dict[str, _SessionRecord] = {}
        self._pending_transport_cleanup: dict[str, _PendingTransportCleanup] = {}
        self._reservations: set[int] = set()
        self._reservations_drained = anyio.Event()
        self._reservations_drained.set()
        self._reservation_sequence = 0
        self._shutting_down = False
        self._shutdown_task: asyncio.Task[_SessionCleanupOutcome] | None = None

    def _now(self) -> float:
        now = float(self._monotonic())
        if not math.isfinite(now):
            raise RuntimeError("monotonic clock returned a non-finite value")
        return now

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if _has_duplicate_security_header(scope):
            await _send_fixed_response(
                send, status=400, body=self._INVALID_REQUEST
            )
            return

        try:
            principal = self._principal_resolver(scope)
        except Exception:
            principal = None
        client_values = _header_values(scope, b"x-client-id")
        client_id = client_values[0] if client_values else None
        if not _valid_principal(principal) or not _valid_client_id(client_id):
            await _send_fixed_response(
                send, status=400, body=self._INVALID_REQUEST
            )
            return

        session_values = _header_values(scope, MCP_SESSION_ID_HEADER)
        if not session_values:
            await self._handle_sessionless_initialize(
                scope,
                receive,
                send,
                principal=principal,
                client_id=client_id,
            )
            return
        session_id = _parse_session_id(session_values[0])
        if session_id is None:
            await self._send_session_not_found(send)
            return
        await self._handle_existing_session(
            scope,
            receive,
            send,
            session_id=session_id,
            principal=principal,
            client_id=client_id,
        )

    async def _handle_sessionless_initialize(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        principal: str,
        client_id: bytes | None,
    ) -> None:
        if scope.get("method", "").upper() != "POST":
            await _send_fixed_response(
                send, status=400, body=self._INVALID_REQUEST
            )
            return
        body = await _read_request_body(
            receive, limit=self._max_initialize_body_bytes
        )
        request_id = _parse_exact_initialize(body) if body is not None else None
        if body is None or request_id is None:
            await _send_fixed_response(
                send, status=400, body=self._INVALID_REQUEST
            )
            return

        reservation = await self._reserve_capacity()
        if reservation is None:
            await _send_fixed_response(
                send, status=503, body=self._CAPACITY_EXHAUSTED
            )
            return

        committed_session_id: str | None = None
        baseline_session_ids: frozenset[str] | None = None
        buffered_response: list[Message] = []
        response_started = False
        response_complete = False
        response_invalid = False
        response_body_bytes = 0

        async def buffered_send(message: Message) -> None:
            nonlocal response_started, response_complete
            nonlocal response_invalid, response_body_bytes
            if response_invalid:
                return
            message_type = message.get("type")
            if message_type == "http.response.start":
                if response_started or response_complete:
                    response_invalid = True
                    buffered_response.clear()
                    return
                status = message.get("status")
                headers = message.get("headers", [])
                if (
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or not isinstance(headers, (list, tuple))
                    or any(
                        not isinstance(item, (list, tuple))
                        or len(item) != 2
                        or not isinstance(item[0], bytes)
                        or not isinstance(item[1], bytes)
                        for item in headers
                    )
                ):
                    response_invalid = True
                    buffered_response.clear()
                    return
                response_started = True
                buffered_response.append(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": list(headers),
                    }
                )
                return
            if message_type != "http.response.body" or not response_started:
                response_invalid = True
                buffered_response.clear()
                return
            if response_complete:
                response_invalid = True
                buffered_response.clear()
                return
            chunk = message.get("body", b"")
            more_body = message.get("more_body", False)
            if not isinstance(chunk, bytes) or not isinstance(more_body, bool):
                response_invalid = True
                buffered_response.clear()
                return
            response_body_bytes += len(chunk)
            if response_body_bytes > self._max_initialize_response_bytes:
                response_invalid = True
                buffered_response.clear()
                return
            buffered_response.append(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": more_body,
                }
            )
            response_complete = not more_body

        try:
            async with self._initialize_lock:
                baseline_session_ids = await self._registry.session_ids()
                try:
                    downstream_failed = False
                    try:
                        await self._app(
                            scope,
                            _replay_body(body, receive),
                            buffered_send,
                        )
                    except Exception:
                        downstream_failed = True

                    current_session_ids = await self._registry.session_ids()
                    new_session_ids = current_session_ids - baseline_session_ids
                    start = buffered_response[0] if buffered_response else None
                    status = (
                        start.get("status")
                        if isinstance(start, Mapping)
                        and start.get("type") == "http.response.start"
                        else None
                    )
                    headers = (
                        start.get("headers", [])
                        if isinstance(start, Mapping)
                        else []
                    )
                    response_session_values = tuple(
                        value
                        for name, value in headers
                        if name.lower() == MCP_SESSION_ID_HEADER
                    )
                    response_session_id = (
                        _parse_session_id(response_session_values[0])
                        if len(response_session_values) == 1
                        else None
                    )
                    content_type_values = tuple(
                        value
                        for name, value in headers
                        if name.lower() == b"content-type"
                    )
                    content_type = (
                        content_type_values[0]
                        if len(content_type_values) == 1
                        else None
                    )
                    response_body = b"".join(
                        message.get("body", b"")
                        for message in buffered_response[1:]
                        if message.get("type") == "http.response.body"
                    )
                    response_sequence_valid = (
                        not downstream_failed
                        and not response_invalid
                        and response_started
                        and response_complete
                        and isinstance(status, int)
                    )
                    initialize_verified = (
                        response_sequence_valid
                        and 200 <= status < 300
                        and response_session_id is not None
                        and response_session_id in new_session_ids
                        and _is_verified_initialize_response(
                            request_id=request_id,
                            content_type=content_type,
                            body=response_body,
                        )
                    )
                    if initialize_verified and await self._commit_session(
                        reservation,
                        session_id=response_session_id,
                        new_session_ids=new_session_ids,
                        principal=principal,
                        client_id=client_id,
                    ):
                        committed_session_id = response_session_id

                    with anyio.CancelScope(shield=True):
                        unbound_session_ids = set(new_session_ids)
                        if committed_session_id is not None:
                            unbound_session_ids.discard(committed_session_id)
                        for session_id in sorted(unbound_session_ids):
                            await self._cleanup_unbound_transport(session_id)

                    if committed_session_id is not None:
                        for message in buffered_response:
                            await send(message)
                    elif (
                        response_sequence_valid
                        and isinstance(status, int)
                        and not 200 <= status < 300
                        and not response_session_values
                    ):
                        for message in buffered_response:
                            await send(message)
                    else:
                        await _send_fixed_response(
                            send, status=503, body=self._UNAVAILABLE
                        )
                except BaseException:
                    with anyio.CancelScope(shield=True):
                        try:
                            current_session_ids = await self._registry.session_ids()
                        except BaseException:
                            current_session_ids = baseline_session_ids
                        unbound_session_ids = set(
                            current_session_ids - baseline_session_ids
                        )
                        if committed_session_id is not None:
                            unbound_session_ids.discard(committed_session_id)
                            try:
                                await self.cleanup_session(committed_session_id)
                            except BaseException:
                                pass
                        for session_id in sorted(unbound_session_ids):
                            await self._cleanup_unbound_transport(session_id)
                    raise
        finally:
            with anyio.CancelScope(shield=True):
                await self._release_reservation(reservation)

    async def _reserve_capacity(self) -> int | None:
        async with self._lock:
            if self._shutting_down:
                return None
            if (
                len(self._sessions)
                + len(self._reservations)
                + len(self._pending_transport_cleanup)
                >= self._max_sessions
            ):
                return None
            self._reservation_sequence += 1
            reservation = self._reservation_sequence
            if not self._reservations:
                self._reservations_drained = anyio.Event()
            self._reservations.add(reservation)
            return reservation

    async def _release_reservation(self, reservation: int) -> None:
        async with self._lock:
            self._reservations.discard(reservation)
            if not self._reservations:
                self._reservations_drained.set()

    async def _commit_session(
        self,
        reservation: int,
        *,
        session_id: str,
        new_session_ids: frozenset[str],
        principal: str,
        client_id: bytes | None,
    ) -> bool:
        if session_id not in new_session_ids or not await self._registry.contains(
            session_id
        ):
            return False
        now = self._now()
        async with self._lock:
            if (
                self._shutting_down
                or reservation not in self._reservations
                or session_id in self._sessions
                or session_id in self._pending_transport_cleanup
            ):
                return False
            self._reservations.remove(reservation)
            self._sessions[session_id] = _SessionRecord(
                binding=MCPSessionBinding(
                    session_id=session_id,
                    principal=principal,
                    client_id=client_id,
                    created_monotonic=now,
                ),
                last_activity_monotonic=now,
            )
            return True

    async def _handle_existing_session(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        session_id: str,
        principal: str,
        client_id: bytes | None,
    ) -> None:
        method = scope.get("method", "").upper()
        call_scope = anyio.CancelScope()
        binding, should_cleanup = await self._enter_session(
            session_id,
            principal=principal,
            client_id=client_id,
            enter_call=method != "DELETE",
            call_scope=call_scope,
        )
        if binding is None:
            if should_cleanup:
                try:
                    await self.cleanup_session(session_id)
                except BaseException:
                    # Expiry must remain indistinguishable from an unknown or
                    # identity-mismatched session. The closing record remains
                    # retryable when cleanup fails.
                    pass
            await self._send_session_not_found(send)
            return

        if method == "DELETE":
            try:
                await self.cleanup_session(session_id)
            except BaseException:
                await _send_fixed_response(
                    send, status=503, body=self._UNAVAILABLE
                )
            else:
                await _send_fixed_response(send, status=204, body=b"")
            return

        request_scope = dict(scope)
        request_scope[MCP_SESSION_BINDING_SCOPE_KEY] = binding
        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            with call_scope:
                await self._app(request_scope, receive, tracked_send)
            if call_scope.cancel_called and not response_started:
                await self._send_session_not_found(send)
        finally:
            with anyio.CancelScope(shield=True):
                if await self._leave_session(session_id, call_scope=call_scope):
                    try:
                        await self.cleanup_session(session_id)
                    except BaseException:
                        pass

    async def _enter_session(
        self,
        session_id: str,
        *,
        principal: str,
        client_id: bytes | None,
        enter_call: bool,
        call_scope: anyio.CancelScope,
    ) -> tuple[MCPSessionBinding | None, bool]:
        now = self._now()
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None or self._shutting_down:
                return None, False
            binding = record.binding
            if binding.principal != principal or binding.client_id != client_id:
                return None, False
            max_expired = now - binding.created_monotonic >= self._max_ttl_seconds
            idle_expired = (
                record.active_calls == 0
                and now - record.last_activity_monotonic >= self._idle_ttl_seconds
            )
            if record.closing or max_expired or idle_expired:
                record.closing = True
                return None, max_expired or record.active_calls == 0
            if enter_call:
                record.active_calls += 1
                record.active_scopes.add(call_scope)
                record.last_activity_monotonic = now
            else:
                record.closing = True
            return binding, False

    async def _leave_session(
        self, session_id: str, *, call_scope: anyio.CancelScope
    ) -> bool:
        now = self._now()
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False
            if call_scope in record.active_scopes:
                record.active_scopes.remove(call_scope)
                record.active_calls -= 1
            max_expired = (
                now - record.binding.created_monotonic >= self._max_ttl_seconds
            )
            if not record.closing:
                if max_expired:
                    record.closing = True
                else:
                    record.last_activity_monotonic = now
            return (
                record.closing
                and (max_expired or record.active_calls == 0)
                and record.cleanup_task is None
                and not record.cleanup_retry_required
            )

    async def reap_expired(self) -> int:
        """Hard-revoke max-expired sessions; reap idle sessions when inactive."""

        now = self._now()
        cleanup: list[str] = []
        async with self._lock:
            for session_id, record in self._sessions.items():
                max_expired = (
                    now - record.binding.created_monotonic
                    >= self._max_ttl_seconds
                )
                idle_expired = (
                    record.active_calls == 0
                    and now - record.last_activity_monotonic
                    >= self._idle_ttl_seconds
                )
                if max_expired or idle_expired:
                    record.closing = True
                if max_expired or (record.closing and record.active_calls == 0):
                    cleanup.append(session_id)
        first_error: BaseException | None = None
        for session_id in cleanup:
            try:
                await self.cleanup_session(session_id)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
        return len(cleanup)

    async def cleanup_session(self, session_id: str) -> bool:
        """Idempotently revoke authority, then remove and terminate transport."""

        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False
            record.closing = True
            if record.cleanup_task is None:
                record.cleanup_retry_required = False
                record.cleanup_task = asyncio.create_task(
                    self._run_session_cleanup(session_id, record)
                )
            cleanup_task = record.cleanup_task
        outcome = await asyncio.shield(cleanup_task)
        if outcome.error is not None:
            raise outcome.error
        return True

    async def _run_session_cleanup(
        self,
        session_id: str,
        record: _SessionRecord,
    ) -> _SessionCleanupOutcome:
        error: BaseException | None = None
        try:
            try:
                await self._runtime_revoker.revoke_session(session_id)
            except BaseException as exc:
                error = exc
            async with self._lock:
                current = self._sessions.get(session_id)
                active_scopes = (
                    tuple(record.active_scopes) if current is record else ()
                )
            for active_scope in active_scopes:
                active_scope.cancel()
            if error is None:
                await self._registry.remove_and_terminate(session_id)
        except BaseException as exc:
            if error is None:
                error = exc
        finally:
            async with self._lock:
                current = self._sessions.get(session_id)
                if current is record and error is None:
                    self._sessions.pop(session_id, None)
                elif current is record:
                    record.cleanup_retry_required = True
                    record.cleanup_task = None
        return _SessionCleanupOutcome(error=error)

    async def _cleanup_unbound_transport(
        self, session_id: str, *, register: bool = True
    ) -> bool:
        async with self._lock:
            pending = self._pending_transport_cleanup.get(session_id)
            if pending is None:
                if not register:
                    return True
                pending = _PendingTransportCleanup()
                self._pending_transport_cleanup[session_id] = pending
            if pending.task is None:
                pending.task = asyncio.create_task(
                    self._run_unbound_transport_cleanup(session_id, pending)
                )
            cleanup_task = pending.task
        return await asyncio.shield(cleanup_task)

    async def _run_unbound_transport_cleanup(
        self,
        session_id: str,
        pending: _PendingTransportCleanup,
    ) -> bool:
        succeeded = False
        try:
            await self._runtime_revoker.revoke_session(session_id)
            await self._registry.remove_and_terminate(session_id)
            succeeded = True
        except BaseException:
            succeeded = False
        finally:
            async with self._lock:
                current = self._pending_transport_cleanup.get(session_id)
                if current is pending:
                    if succeeded:
                        self._pending_transport_cleanup.pop(session_id, None)
                    else:
                        pending.task = None
        return succeeded

    async def shutdown(self) -> None:
        """Idempotently reject admission and clean every committed session."""

        async with self._lock:
            self._shutting_down = True
            for record in self._sessions.values():
                record.closing = True
            if self._shutdown_task is None:
                self._shutdown_task = asyncio.create_task(self._run_shutdown())
            shutdown_task = self._shutdown_task
        outcome = await asyncio.shield(shutdown_task)
        if outcome.error is not None:
            raise outcome.error

    async def _run_shutdown(self) -> _SessionCleanupOutcome:
        """Own the complete shutdown independently of any individual waiter."""

        first_error: BaseException | None = None
        try:
            async with self._lock:
                reservations_drained = self._reservations_drained
            await reservations_drained.wait()
            async with self._lock:
                session_ids = tuple(self._sessions)
                pending_transport_ids = tuple(self._pending_transport_cleanup)
                for record in self._sessions.values():
                    record.closing = True
            for session_id in session_ids:
                try:
                    await self.cleanup_session(session_id)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            for session_id in pending_transport_ids:
                if not await self._cleanup_unbound_transport(
                    session_id, register=False
                ):
                    if first_error is None:
                        first_error = RuntimeError(
                            "stateful MCP transport cleanup remains pending"
                        )
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        finally:
            if first_error is not None:
                async with self._lock:
                    if self._shutdown_task is asyncio.current_task():
                        self._shutdown_task = None
        return _SessionCleanupOutcome(error=first_error)

    async def _send_session_not_found(self, send: Send) -> None:
        await _send_fixed_response(
            send, status=404, body=self._SESSION_NOT_FOUND
        )
