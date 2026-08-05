from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from unigrok_public import remote_auth, server

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "boundary-test", "version": "1"},
    },
}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_LOCAL_SETTINGS = {
    "enable_dns_rebinding_protection": True,
    "allowed_hosts": ["127.0.0.1:*", "localhost:*", "[::1]:*"],
    "allowed_origins": [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ],
}


def _transport_app() -> object:
    mcp = FastMCP(
        "transport-boundary-test",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=False,
        transport_security=server._transport_security_settings(),
    )
    mcp._mcp_server.version = server.__version__
    return mcp.streamable_http_app()


def test_mcp_transport_security_is_explicitly_pinned_to_loopback() -> None:
    assert server.mcp.settings.transport_security.model_dump() == _LOCAL_SETTINGS


def test_mcp_transport_rejects_host_and_origin_rebinding() -> None:
    with TestClient(server.mcp.streamable_http_app()) as client:
        hostile_host = client.post(
            "/mcp",
            headers={**_HEADERS, "Host": "attacker.invalid"},
            json=_INITIALIZE,
        )
        hostile_origin = client.post(
            "/mcp",
            headers={
                **_HEADERS,
                "Host": "127.0.0.1:4765",
                "Origin": "https://attacker.invalid",
            },
            json=_INITIALIZE,
        )
        local = client.post(
            "/mcp",
            headers={
                **_HEADERS,
                "Host": "127.0.0.1:4765",
                "Origin": "http://127.0.0.1:4765",
            },
            json=_INITIALIZE,
        )

    assert hostile_host.status_code == 421
    assert hostile_origin.status_code == 403
    assert local.status_code == 200
    assert f'"version":"{server.__version__}"' in local.text


def test_cloudrun_transport_adds_only_the_configured_public_authority(
    monkeypatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv(
        "UNIGROK_PUBLIC_MCP_URL", "https://MCP.Example.Test./mcp"
    )

    assert server._transport_security_settings().model_dump() == {
        **_LOCAL_SETTINGS,
        "allowed_hosts": [
            *_LOCAL_SETTINGS["allowed_hosts"],
            "mcp.example.test",
            "mcp.example.test:443",
        ],
        "allowed_origins": [
            *_LOCAL_SETTINGS["allowed_origins"],
            "https://mcp.example.test",
            "https://mcp.example.test:443",
        ],
    }


def test_cloudrun_transport_preserves_a_configured_nondefault_port(
    monkeypatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv(
        "UNIGROK_PUBLIC_MCP_URL", "https://mcp.example.test:8443/mcp"
    )

    settings = server._transport_security_settings().model_dump()

    assert settings["allowed_hosts"] == [
        *_LOCAL_SETTINGS["allowed_hosts"],
        "mcp.example.test:8443",
    ]
    assert settings["allowed_origins"] == [
        *_LOCAL_SETTINGS["allowed_origins"],
        "https://mcp.example.test:8443",
    ]


def test_cloudrun_transport_fails_closed_for_an_internal_public_resource(
    monkeypatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", "https://mcp.internal/mcp")

    assert server._transport_security_settings().model_dump() == _LOCAL_SETTINGS


def test_cloudrun_transport_accepts_configured_authority_and_rejects_others(
    monkeypatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", "https://mcp.example.test/mcp")

    with TestClient(_transport_app()) as client:
        public = client.post(
            "/mcp",
            headers={
                **_HEADERS,
                "Host": "mcp.example.test",
                "Origin": "https://mcp.example.test",
            },
            json=_INITIALIZE,
        )
        public_default_port = client.post(
            "/mcp",
            headers={
                **_HEADERS,
                "Host": "mcp.example.test:443",
                "Origin": "https://mcp.example.test:443",
            },
            json=_INITIALIZE,
        )
        hostile_host = client.post(
            "/mcp",
            headers={**_HEADERS, "Host": "attacker.invalid"},
            json=_INITIALIZE,
        )
        hostile_origin = client.post(
            "/mcp",
            headers={
                **_HEADERS,
                "Host": "mcp.example.test",
                "Origin": "https://attacker.invalid",
            },
            json=_INITIALIZE,
        )

    assert public.status_code in {200, 406}
    assert public_default_port.status_code in {200, 406}
    assert hostile_host.status_code == 421
    assert hostile_origin.status_code == 403


def test_cloudrun_origin_guard_uses_the_configured_public_authority(
    monkeypatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv(
        "UNIGROK_PUBLIC_MCP_URL", "https://MCP.Example.Test./mcp"
    )

    assert remote_auth._origin_allowed("https://mcp.example.test") is True
    assert remote_auth._origin_allowed("https://mcp.example.test:443") is True
    assert remote_auth._origin_allowed("https://attacker.invalid") is False


def test_local_origin_guard_does_not_trust_the_hosted_public_authority(
    monkeypatch,
) -> None:
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", "https://mcp.example.test/mcp")

    assert remote_auth._origin_allowed("https://mcp.example.test") is False
