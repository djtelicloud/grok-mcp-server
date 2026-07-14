from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from src.mcp_session_guard import (
    MCP_SESSION_BINDING_SCOPE_KEY,
    MCP126SessionTransportRegistry,
    MCPSessionBinding,
    StatefulMCPSessionGuard,
)


def initialize_payload(*, request_id: str | int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"sampling": {}},
            "clientInfo": {"name": "guard-test", "version": "1.0"},
        },
    }


def header(scope: Mapping[str, Any], name: bytes) -> bytes | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value
    return None


def principal_from_scope(scope: Mapping[str, Any]) -> str:
    authorization = header(scope, b"authorization")
    suffix = authorization.decode("ascii") if authorization is not None else "anon"
    return f"server:{suffix}"


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeTransport:
    def __init__(self, session_id: str, events: list[str]) -> None:
        self.session_id = session_id
        self.events = events
        self.terminated = False

    async def terminate(self) -> None:
        self.events.append(f"terminate:{self.session_id}")
        self.terminated = True


class FakeRegistry:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.transports: dict[str, FakeTransport] = {}

    def add(self, session_id: str) -> None:
        self.transports[session_id] = FakeTransport(session_id, self.events)

    async def contains(self, session_id: str) -> bool:
        return session_id in self.transports

    async def session_ids(self) -> frozenset[str]:
        return frozenset(self.transports)

    async def remove_and_terminate(self, session_id: str) -> bool:
        self.events.append(f"remove:{session_id}")
        transport = self.transports.pop(session_id, None)
        if transport is None:
            return False
        await transport.terminate()
        return True


class BlockingSnapshotRegistry(FakeRegistry):
    def __init__(self, events: list[str], *, block_call: int) -> None:
        super().__init__(events)
        self.block_call = block_call
        self.snapshot_calls = 0
        self.blocked = asyncio.Event()

    async def session_ids(self) -> frozenset[str]:
        self.snapshot_calls += 1
        if self.snapshot_calls == self.block_call:
            self.blocked.set()
            await asyncio.Event().wait()
        return await super().session_ids()


class FakeRevoker:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def revoke_session(self, session_id: str) -> None:
        self.events.append(f"revoke:{session_id}")


class FlakyRevoker(FakeRevoker):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.failures = 1

    async def revoke_session(self, session_id: str) -> None:
        self.events.append(f"revoke:{session_id}")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("revocation failed")


class PendingThenBlockingRevoker(FakeRevoker):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.retry_started = asyncio.Event()
        self.retry_release = asyncio.Event()

    async def revoke_session(self, session_id: str) -> None:
        self.calls += 1
        self.events.append(f"revoke:{session_id}")
        if self.calls == 1:
            raise RuntimeError("initial revocation failed")
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.retry_started.set()
        await self.retry_release.wait()
        self.concurrent -= 1


class BlockingCommittedRevoker(FakeRevoker):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def revoke_session(self, session_id: str) -> None:
        self.calls += 1
        self.events.append(f"revoke:{session_id}")
        self.started.set()
        await self.release.wait()


class FakeStatefulApp:
    def __init__(self, registry: FakeRegistry) -> None:
        self.registry = registry
        self.calls = 0
        self.initializations = 0
        self.bindings: list[MCPSessionBinding] = []
        self.response_session_headers: list[tuple[bytes, bytes]] | None = None
        self.initialize_response_payload: dict[str, Any] | None = None
        self.initialize_status = 200

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        if header(scope, b"mcp-session-id") is None:
            self.initializations += 1
            # Exercise the guard's request-body replay, not only its parser.
            request = await receive()
            assert request["type"] == "http.request"
            initialize_request = json.loads(request["body"])
            assert initialize_request["method"] == "initialize"
            session_id = f"session-{self.initializations}"
            self.registry.add(session_id)
            response_headers = self.response_session_headers
            if response_headers is None:
                response_headers = [(b"mcp-session-id", session_id.encode())]
            await send(
                {
                    "type": "http.response.start",
                    "status": self.initialize_status,
                    "headers": response_headers,
                }
            )
            response = self.initialize_response_payload or {
                "jsonrpc": "2.0",
                "id": initialize_request["id"],
                "result": {
                    "protocolVersion": initialize_request["params"][
                        "protocolVersion"
                    ],
                    "capabilities": {},
                    "serverInfo": {"name": "guard-test", "version": "1.0"},
                },
            }
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps(response).encode(),
                }
            )
            return

        binding = scope.get(MCP_SESSION_BINDING_SCOPE_KEY)
        assert isinstance(binding, MCPSessionBinding)
        self.bindings.append(binding)
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"{}"})


class BlockingStatefulApp(FakeStatefulApp):
    def __init__(self, registry: FakeRegistry) -> None:
        super().__init__(registry)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        if (
            header(scope, b"mcp-session-id") is not None
            and scope.get("path") == "/block"
        ):
            self.calls += 1
            binding = scope.get(MCP_SESSION_BINDING_SCOPE_KEY)
            assert isinstance(binding, MCPSessionBinding)
            self.bindings.append(binding)
            self.started.set()
            await self.release.wait()
            await send(
                {"type": "http.response.start", "status": 200, "headers": []}
            )
            await send({"type": "http.response.body", "body": b"{}"})
            return
        await super().__call__(scope, receive, send)


class BlockingInitializeApp(FakeStatefulApp):
    def __init__(self, registry: FakeRegistry) -> None:
        super().__init__(registry)
        self.initialize_started = asyncio.Event()
        self.initialize_release = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        if header(scope, b"mcp-session-id") is None:
            self.initialize_started.set()
            await self.initialize_release.wait()
        await super().__call__(scope, receive, send)


class NoResponseInitializeApp(FakeStatefulApp):
    async def __call__(self, scope, receive, send) -> None:
        if header(scope, b"mcp-session-id") is not None:
            await super().__call__(scope, receive, send)
            return
        self.calls += 1
        self.initializations += 1
        await receive()
        self.registry.add(f"session-{self.initializations}")


class BodyFirstInitializeApp(FakeStatefulApp):
    async def __call__(self, scope, receive, send) -> None:
        if header(scope, b"mcp-session-id") is not None:
            await super().__call__(scope, receive, send)
            return
        self.calls += 1
        self.initializations += 1
        await receive()
        self.registry.add(f"session-{self.initializations}")
        await send({"type": "http.response.body", "body": b"{}"})


def build_guard(
    *,
    max_sessions: int = 4,
    idle_ttl_seconds: float = 10,
    max_ttl_seconds: float = 100,
    clock: FakeClock | None = None,
    app_type=FakeStatefulApp,
    revoker_type=FakeRevoker,
):
    events: list[str] = []
    registry = FakeRegistry(events)
    app = app_type(registry)
    clock = clock or FakeClock()
    guard = StatefulMCPSessionGuard(
        app,
        registry=registry,
        runtime_revoker=revoker_type(events),
        principal_resolver=principal_from_scope,
        max_sessions=max_sessions,
        idle_ttl_seconds=idle_ttl_seconds,
        max_ttl_seconds=max_ttl_seconds,
        monotonic=clock,
    )
    return guard, app, registry, events, clock


def build_guard_with_registry(registry: FakeRegistry):
    app = FakeStatefulApp(registry)
    clock = FakeClock()
    guard = StatefulMCPSessionGuard(
        app,
        registry=registry,
        runtime_revoker=FakeRevoker(registry.events),
        principal_resolver=principal_from_scope,
        max_sessions=2,
        idle_ttl_seconds=10,
        max_ttl_seconds=100,
        monotonic=clock,
    )
    return guard, app


async def initialize(
    client: httpx.AsyncClient,
    *,
    headers: list[tuple[str, str]] | dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post("/mcp", json=initialize_payload(), headers=headers)


@pytest.mark.asyncio
async def test_exact_initialize_is_only_sessionless_request_reaching_downstream():
    guard, app, registry, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client, headers={"X-Client-ID": "codex"})

    assert response.status_code == 200
    assert response.headers["mcp-session-id"] == "session-1"
    assert app.calls == 1
    assert set(registry.transports) == {"session-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "content"),
    [
        ("GET", None),
        ("POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        ("POST", [initialize_payload()]),
        ("POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
    ],
)
async def test_sessionless_non_initialize_malformed_and_batch_are_rejected(
    method: str, content: Any
):
    guard, app, registry, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await client.request(method, "/mcp", json=content)

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert app.calls == 0
    assert registry.transports == {}


@pytest.mark.asyncio
async def test_duplicate_json_keys_are_rejected_before_downstream_allocation():
    guard, app, registry, _, _ = build_guard()
    body = (
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"initialize",'
        b'"params":{"protocolVersion":"x","capabilities":{},'
        b'"clientInfo":{"name":"n","version":"v"}}}'
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await client.post("/mcp", content=body)

    assert response.status_code == 400
    assert app.calls == 0
    assert registry.transports == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
        b'"params":{"protocolVersion":"x","capabilities":{"x":NaN},'
        b'"clientInfo":{"name":"n","version":"v"}}}',
        json.dumps(
            {
                **initialize_payload(),
                "params": {
                    **initialize_payload()["params"],
                    "capabilities": {
                        "roots": {"listChanged": "not-a-boolean"}
                    },
                },
            }
        ).encode(),
    ],
)
async def test_non_rfc_json_and_nested_invalid_initialize_are_rejected(body):
    guard, app, registry, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await client.post("/mcp", content=body)

    assert response.status_code == 400
    assert app.calls == 0
    assert registry.transports == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("client_id", ["", "   ", "codex/other"])
async def test_present_client_id_must_be_nonempty_safe_identity(client_id):
    guard, app, registry, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client, headers={"X-Client-ID": client_id})

    assert response.status_code == 400
    assert app.calls == 0
    assert registry.transports == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "one"), ("authorization", "two")],
        [("X-Client-ID", "codex"), ("x-client-id", "other")],
        [("Mcp-Session-Id", "session-1"), ("mcp-session-id", "session-2")],
    ],
)
async def test_duplicate_security_headers_are_rejected(headers):
    guard, app, registry, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp", json=initialize_payload(), headers=headers
        )

    assert response.status_code == 400
    assert app.calls == 0
    assert registry.transports == {}


@pytest.mark.asyncio
async def test_capacity_reservation_prevents_second_transport_allocation():
    guard, app, registry, _, _ = build_guard(max_sessions=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        first = await initialize(client)
        second = await initialize(client)

    assert first.status_code == 200
    assert second.status_code == 503
    assert second.json() == {"error": "session_capacity_exhausted"}
    assert app.initializations == 1
    assert set(registry.transports) == {"session-1"}


@pytest.mark.asyncio
async def test_inflight_reservation_closes_concurrent_capacity_race():
    guard, app, registry, _, _ = build_guard(
        max_sessions=1, app_type=BlockingInitializeApp
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(initialize(client))
        await asyncio.wait_for(app.initialize_started.wait(), timeout=1)
        second = await initialize(client)
        assert second.status_code == 503
        assert app.initializations == 0
        assert registry.transports == {}
        app.initialize_release.set()
        first = await asyncio.wait_for(first_task, timeout=1)

    assert first.status_code == 200
    assert app.initializations == 1
    assert set(registry.transports) == {"session-1"}


@pytest.mark.asyncio
async def test_cancel_while_queued_for_initialize_lock_releases_reservation():
    guard, app, registry, _, _ = build_guard(max_sessions=2)
    await guard._initialize_lock.acquire()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guard), base_url="http://test"
        ) as client:
            request = asyncio.create_task(initialize(client))
            for _ in range(100):
                if guard._reservations:
                    break
                await asyncio.sleep(0)
            assert len(guard._reservations) == 1
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
    finally:
        guard._initialize_lock.release()

    assert guard._reservations == set()
    assert app.calls == 0
    assert registry.transports == {}
    await asyncio.wait_for(guard.shutdown(), timeout=1)


@pytest.mark.asyncio
async def test_cancel_during_baseline_snapshot_releases_without_dispatch():
    events: list[str] = []
    registry = BlockingSnapshotRegistry(events, block_call=1)
    guard, app = build_guard_with_registry(registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        request = asyncio.create_task(initialize(client))
        await asyncio.wait_for(registry.blocked.wait(), timeout=1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert guard._reservations == set()
    assert app.calls == 0
    assert registry.transports == {}
    assert events == []
    await asyncio.wait_for(guard.shutdown(), timeout=1)


@pytest.mark.asyncio
async def test_cancel_during_post_allocation_snapshot_cleans_before_reraise():
    events: list[str] = []
    registry = BlockingSnapshotRegistry(events, block_call=2)
    guard, app = build_guard_with_registry(registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        request = asyncio.create_task(initialize(client))
        await asyncio.wait_for(registry.blocked.wait(), timeout=1)
        assert app.initializations == 1
        assert set(registry.transports) == {"session-1"}
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert guard._reservations == set()
    assert registry.transports == {}
    assert guard._pending_transport_cleanup == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]
    await asyncio.wait_for(guard.shutdown(), timeout=1)


@pytest.mark.asyncio
async def test_failed_initialize_releases_reservation_and_cleans_sdk_transport():
    guard, app, registry, events, _ = build_guard(max_sessions=1)
    app.initialize_status = 400
    app.response_session_headers = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        first = await initialize(client)
        second = await initialize(client)

    assert first.status_code == 400
    assert second.status_code == 400
    assert app.initializations == 2
    assert registry.transports == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
        "revoke:session-2",
        "remove:session-2",
        "terminate:session-2",
    ]


@pytest.mark.asyncio
async def test_success_without_one_valid_session_header_fails_closed_and_cleans():
    guard, app, registry, events, _ = build_guard()
    app.response_session_headers = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client)

    assert response.status_code == 503
    assert registry.transports == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("app_type", [NoResponseInitializeApp, BodyFirstInitializeApp])
async def test_missing_or_body_first_initialize_response_is_explicit_503_and_cleaned(
    app_type,
):
    guard, app, registry, events, _ = build_guard(app_type=app_type)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client)

    assert response.status_code == 503
    assert response.json() == {"error": "session_guard_unavailable"}
    assert app.initializations == 1
    assert registry.transports == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_jsonrpc_initialize_error_is_never_committed_despite_2xx_header():
    guard, app, registry, events, _ = build_guard()
    app.initialize_response_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "invalid params"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client)
        followup = await client.post(
            "/mcp", headers={"Mcp-Session-Id": "session-1"}
        )

    assert response.status_code == 503
    assert followup.status_code == 404
    assert registry.transports == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_initialize_cannot_claim_a_preexisting_registry_transport():
    guard, app, registry, events, _ = build_guard()
    registry.add("preexisting")
    app.response_session_headers = [(b"mcp-session-id", b"preexisting")]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client)
        followup = await client.post(
            "/mcp", headers={"Mcp-Session-Id": "preexisting"}
        )

    assert response.status_code == 503
    assert followup.status_code == 404
    assert set(registry.transports) == {"preexisting"}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_failed_unbound_cleanup_is_pending_and_shutdown_retries_it():
    guard, app, registry, events, _ = build_guard(
        max_sessions=1,
        app_type=NoResponseInitializeApp,
        revoker_type=FlakyRevoker,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        response = await initialize(client)
        capacity_blocked = await initialize(client)
        assert response.status_code == 503
        assert capacity_blocked.status_code == 503
        assert app.initializations == 1
        assert set(registry.transports) == {"session-1"}
        assert set(guard._pending_transport_cleanup) == {"session-1"}
        await guard.shutdown()

    assert registry.transports == {}
    assert guard._pending_transport_cleanup == {}
    assert events == [
        "revoke:session-1",
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_concurrent_shutdowns_deduplicate_pending_transport_cleanup():
    guard, _, registry, events, _ = build_guard(
        max_sessions=1,
        app_type=NoResponseInitializeApp,
        revoker_type=PendingThenBlockingRevoker,
    )
    revoker = guard._runtime_revoker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        assert (await initialize(client)).status_code == 503
    assert set(guard._pending_transport_cleanup) == {"session-1"}

    first = asyncio.create_task(guard.shutdown())
    second = asyncio.create_task(guard.shutdown())
    await asyncio.wait_for(revoker.retry_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert revoker.calls == 2
    assert revoker.max_concurrent == 1
    revoker.retry_release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

    assert registry.transports == {}
    assert guard._pending_transport_cleanup == {}
    assert events == [
        "revoke:session-1",
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_cancelled_shutdown_does_not_cancel_claimed_pending_cleanup():
    guard, _, registry, events, _ = build_guard(
        max_sessions=1,
        app_type=NoResponseInitializeApp,
        revoker_type=PendingThenBlockingRevoker,
    )
    revoker = guard._runtime_revoker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        assert (await initialize(client)).status_code == 503

    shutdown = asyncio.create_task(guard.shutdown())
    await asyncio.wait_for(revoker.retry_started.wait(), timeout=1)
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    revoker.retry_release.set()
    for _ in range(100):
        if not guard._pending_transport_cleanup:
            break
        await asyncio.sleep(0)

    assert guard._pending_transport_cleanup == {}
    assert registry.transports == {}
    assert revoker.calls == 2
    assert revoker.max_concurrent == 1
    await guard.shutdown()
    assert events == [
        "revoke:session-1",
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_session_binding_uses_exact_server_principal_and_client_id():
    guard, app, _, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(
            client,
            headers={"Authorization": "principal-a", "X-Client-ID": "Codex"},
        )
        session_id = initialized.headers["mcp-session-id"]
        accepted = await client.post(
            "/mcp",
            headers={
                "Authorization": "principal-a",
                "X-Client-ID": "Codex",
                "Mcp-Session-Id": session_id,
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        wrong_case = await client.post(
            "/mcp",
            headers={
                "Authorization": "principal-a",
                "X-Client-ID": "codex",
                "Mcp-Session-Id": session_id,
            },
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        )

    assert accepted.status_code == 200
    assert wrong_case.status_code == 404
    assert app.bindings[0].principal == "server:principal-a"
    assert app.bindings[0].client_id == b"Codex"


@pytest.mark.asyncio
async def test_absent_client_id_is_distinct_from_present_client_id():
    guard, _, _, _, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        response = await client.post(
            "/mcp",
            headers={
                "X-Client-ID": "codex",
                "Mcp-Session-Id": initialized.headers["mcp-session-id"],
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )

    assert response.status_code == 404
    assert response.json() == {"error": "session_not_found"}


@pytest.mark.asyncio
async def test_unknown_expired_and_identity_mismatch_share_generic_404():
    clock = FakeClock()
    guard, _, _, _, _ = build_guard(
        clock=clock, idle_ttl_seconds=5, max_ttl_seconds=100
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client, headers={"Authorization": "a"})
        session_id = initialized.headers["mcp-session-id"]
        unknown = await client.post(
            "/mcp",
            headers={"Authorization": "a", "Mcp-Session-Id": "unknown"},
        )
        mismatch = await client.post(
            "/mcp",
            headers={"Authorization": "b", "Mcp-Session-Id": session_id},
        )
        clock.advance(5)
        expired = await client.post(
            "/mcp",
            headers={"Authorization": "a", "Mcp-Session-Id": session_id},
        )

    assert {unknown.status_code, mismatch.status_code, expired.status_code} == {404}
    assert unknown.content == mismatch.content == expired.content


@pytest.mark.asyncio
async def test_expired_cleanup_failure_still_matches_generic_404_and_is_retryable():
    clock = FakeClock()
    guard, _, registry, events, _ = build_guard(
        clock=clock,
        idle_ttl_seconds=5,
        max_ttl_seconds=100,
        revoker_type=FlakyRevoker,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client, headers={"Authorization": "a"})
        session_id = initialized.headers["mcp-session-id"]
        unknown = await client.post(
            "/mcp",
            headers={"Authorization": "a", "Mcp-Session-Id": "unknown"},
        )
        mismatch = await client.post(
            "/mcp",
            headers={"Authorization": "b", "Mcp-Session-Id": session_id},
        )
        clock.advance(5)
        expired = await client.post(
            "/mcp",
            headers={"Authorization": "a", "Mcp-Session-Id": session_id},
        )
        assert session_id in registry.transports
        await guard.shutdown()

    assert {unknown.status_code, mismatch.status_code, expired.status_code} == {404}
    assert unknown.content == mismatch.content == expired.content
    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_idle_ttl_uses_completion_activity_and_reaper_cleanup_order():
    clock = FakeClock()
    guard, _, registry, events, _ = build_guard(
        clock=clock, idle_ttl_seconds=10, max_ttl_seconds=100
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        clock.advance(5)
        active = await client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        clock.advance(9)
        assert await guard.reap_expired() == 0
        clock.advance(1)
        assert await guard.reap_expired() == 1

    assert active.status_code == 200
    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_max_ttl_hard_revokes_and_cancels_active_call_at_deadline():
    clock = FakeClock()
    guard, app, registry, events, _ = build_guard(
        clock=clock,
        idle_ttl_seconds=5,
        max_ttl_seconds=10,
        app_type=BlockingStatefulApp,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        blocked = asyncio.create_task(
            client.post(
                "/block",
                headers={"Mcp-Session-Id": session_id},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )
        await asyncio.wait_for(app.started.wait(), timeout=1)
        clock.advance(10)
        assert await guard.reap_expired() == 1
        completed = await asyncio.wait_for(blocked, timeout=1)

    assert completed.status_code == 404
    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_max_ttl_cancels_active_call_when_revocation_needs_retry():
    clock = FakeClock()
    guard, app, registry, events, _ = build_guard(
        clock=clock,
        idle_ttl_seconds=5,
        max_ttl_seconds=10,
        app_type=BlockingStatefulApp,
        revoker_type=FlakyRevoker,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        blocked = asyncio.create_task(
            client.post(
                "/block",
                headers={"Mcp-Session-Id": session_id},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )
        await asyncio.wait_for(app.started.wait(), timeout=1)
        clock.advance(10)

        with pytest.raises(RuntimeError, match="revocation failed"):
            await guard.reap_expired()
        completed = await asyncio.wait_for(blocked, timeout=1)

        assert completed.status_code == 404
        assert session_id in registry.transports
        assert events == [f"revoke:{session_id}"]
        assert await guard.cleanup_session(session_id) is True

    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_delete_and_direct_cleanup_are_idempotent_and_revoke_first():
    guard, _, registry, events, _ = build_guard()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        deleted = await client.delete(
            "/mcp", headers={"Mcp-Session-Id": session_id}
        )
        repeated = await client.delete(
            "/mcp", headers={"Mcp-Session-Id": session_id}
        )
        assert await guard.cleanup_session(session_id) is False

    assert deleted.status_code == 204
    assert repeated.status_code == 404
    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_delete_cleanup_failure_returns_503_and_retains_retryable_tombstone():
    guard, _, registry, events, _ = build_guard(revoker_type=FlakyRevoker)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        failed = await client.delete(
            "/mcp", headers={"Mcp-Session-Id": session_id}
        )
        assert failed.status_code == 503
        assert session_id in registry.transports
        retry = await client.delete(
            "/mcp", headers={"Mcp-Session-Id": session_id}
        )

    assert retry.status_code == 404
    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_failed_revocation_blocks_transport_removal_and_can_be_retried():
    guard, _, registry, events, _ = build_guard(revoker_type=FlakyRevoker)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        with pytest.raises(RuntimeError, match="revocation failed"):
            await guard.cleanup_session(session_id)
        assert session_id in registry.transports
        assert events == [f"revoke:{session_id}"]
        assert await guard.cleanup_session(session_id) is True

    assert registry.transports == {}
    assert events == [
        f"revoke:{session_id}",
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_cancelling_cleanup_waiter_does_not_cancel_committed_cleanup_effect():
    guard, _, registry, events, _ = build_guard(
        revoker_type=BlockingCommittedRevoker
    )
    revoker = guard._runtime_revoker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
    session_id = initialized.headers["mcp-session-id"]

    waiter = asyncio.create_task(guard.cleanup_session(session_id))
    await asyncio.wait_for(revoker.started.wait(), timeout=1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    revoker.release.set()
    for _ in range(100):
        if session_id not in registry.transports:
            break
        await asyncio.sleep(0)

    assert session_id not in registry.transports
    assert revoker.calls == 1
    assert await guard.cleanup_session(session_id) is False
    assert events == [
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_cancelling_shutdown_waiter_does_not_cancel_committed_cleanup():
    guard, _, registry, events, _ = build_guard(
        revoker_type=BlockingCommittedRevoker
    )
    revoker = guard._runtime_revoker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialized = await initialize(client)
    session_id = initialized.headers["mcp-session-id"]

    shutdown = asyncio.create_task(guard.shutdown())
    await asyncio.wait_for(revoker.started.wait(), timeout=1)
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    revoker.release.set()
    for _ in range(100):
        if session_id not in registry.transports:
            break
        await asyncio.sleep(0)

    assert session_id not in registry.transports
    assert revoker.calls == 1
    await guard.shutdown()
    assert events == [
        f"revoke:{session_id}",
        f"remove:{session_id}",
        f"terminate:{session_id}",
    ]


@pytest.mark.asyncio
async def test_cancelled_shutdown_still_cleans_all_sessions_without_retry():
    guard, _, registry, events, _ = build_guard(
        max_sessions=2, revoker_type=BlockingCommittedRevoker
    )
    revoker = guard._runtime_revoker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        first = await initialize(client)
        second = await initialize(client)
    session_ids = (
        first.headers["mcp-session-id"],
        second.headers["mcp-session-id"],
    )

    shutdown_waiter = asyncio.create_task(guard.shutdown())
    await asyncio.wait_for(revoker.started.wait(), timeout=1)
    master_cleanup = guard._shutdown_task
    assert master_cleanup is not None
    assert revoker.calls == 1
    shutdown_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown_waiter

    revoker.release.set()
    outcome = await asyncio.wait_for(
        asyncio.shield(master_cleanup), timeout=1
    )

    assert outcome.error is None
    assert registry.transports == {}
    assert revoker.calls == 2
    assert events == [
        f"revoke:{session_ids[0]}",
        f"remove:{session_ids[0]}",
        f"terminate:{session_ids[0]}",
        f"revoke:{session_ids[1]}",
        f"remove:{session_ids[1]}",
        f"terminate:{session_ids[1]}",
    ]


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_cleans_every_session():
    guard, app, registry, events, _ = build_guard(max_sessions=2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        assert (await initialize(client)).status_code == 200
        assert (await initialize(client)).status_code == 200
        await guard.shutdown()
        await guard.shutdown()
        refused = await initialize(client)

    assert refused.status_code == 503
    assert app.initializations == 2
    assert registry.transports == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
        "revoke:session-2",
        "remove:session-2",
        "terminate:session-2",
    ]


@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_initialize_to_fail_closed_and_cleanup():
    guard, app, registry, events, _ = build_guard(
        max_sessions=1, app_type=BlockingInitializeApp
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as client:
        initialize_task = asyncio.create_task(initialize(client))
        await asyncio.wait_for(app.initialize_started.wait(), timeout=1)
        shutdown_task = asyncio.create_task(guard.shutdown())
        await asyncio.sleep(0)
        assert shutdown_task.done() is False
        app.initialize_release.set()
        response = await asyncio.wait_for(initialize_task, timeout=1)
        await asyncio.wait_for(shutdown_task, timeout=1)

    assert response.status_code == 503
    assert registry.transports == {}
    assert events == [
        "revoke:session-1",
        "remove:session-1",
        "terminate:session-1",
    ]


@pytest.mark.asyncio
async def test_sdk_private_registry_is_instance_scoped_and_terminates_after_removal():
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    events: list[str] = []
    manager = StreamableHTTPSessionManager(app=object())
    transport = FakeTransport("session-a", events)
    manager._server_instances["session-a"] = transport
    registry = MCP126SessionTransportRegistry(manager)

    assert await registry.session_ids() == frozenset({"session-a"})
    assert await registry.contains("session-a") is True
    assert await registry.remove_and_terminate("session-a") is True
    assert manager._server_instances == {}
    assert events == ["terminate:session-a"]


@pytest.mark.asyncio
async def test_guard_interoperates_with_real_mcp_126_stateful_asgi():
    from mcp.server.fastmcp import FastMCP

    events: list[str] = []
    mcp = FastMCP("guard-integration", stateless_http=False)
    mcp_app = mcp.streamable_http_app()
    registry = MCP126SessionTransportRegistry(mcp.session_manager)
    guard = StatefulMCPSessionGuard(
        mcp_app,
        registry=registry,
        runtime_revoker=FakeRevoker(events),
        principal_resolver=principal_from_scope,
    )

    async with mcp_app.router.lifespan_context(mcp_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guard),
            base_url="http://127.0.0.1:8000",
        ) as client:
            invalid_payload = initialize_payload()
            invalid_payload["params"]["capabilities"] = {
                "roots": {"listChanged": "not-a-boolean"}
            }
            invalid = await client.post(
                "/mcp",
                json=invalid_payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert invalid.status_code == 400
            assert await registry.session_ids() == frozenset()
            async with asyncio.timeout(5):
                initialized = await initialize(
                    client,
                    headers={"Accept": "application/json, text/event-stream"},
                )
            session_id = initialized.headers["mcp-session-id"]
            assert initialized.status_code == 200
            assert session_id in await registry.session_ids()
            async with asyncio.timeout(5):
                deleted = await client.delete(
                    "/mcp", headers={"Mcp-Session-Id": session_id}
                )

    assert deleted.status_code == 204
    assert await registry.session_ids() == frozenset()
    assert events == [f"revoke:{session_id}"]


def test_sdk_private_registry_rejects_unreviewed_minor(monkeypatch):
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    monkeypatch.setattr("src.mcp_session_guard.metadata.version", lambda _: "1.27.0")
    manager = StreamableHTTPSessionManager(app=object())

    with pytest.raises(RuntimeError, match="explicit SDK minor review"):
        MCP126SessionTransportRegistry(manager)
