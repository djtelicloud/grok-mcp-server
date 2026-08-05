from __future__ import annotations

from starlette.testclient import TestClient

from unigrok_public import server


def test_mcp_transport_security_is_explicitly_pinned_to_loopback() -> None:
    assert server.mcp.settings.transport_security.model_dump() == {
        "enable_dns_rebinding_protection": True,
        "allowed_hosts": ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        "allowed_origins": [
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    }


def test_mcp_transport_rejects_host_and_origin_rebinding() -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "boundary-test", "version": "1"},
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(server.mcp.streamable_http_app()) as client:
        hostile_host = client.post(
            "/mcp",
            headers={**headers, "Host": "attacker.invalid"},
            json=initialize,
        )
        hostile_origin = client.post(
            "/mcp",
            headers={
                **headers,
                "Host": "127.0.0.1:4765",
                "Origin": "https://attacker.invalid",
            },
            json=initialize,
        )
        local = client.post(
            "/mcp",
            headers={
                **headers,
                "Host": "127.0.0.1:4765",
                "Origin": "http://127.0.0.1:4765",
            },
            json=initialize,
        )

    assert hostile_host.status_code == 421
    assert hostile_origin.status_code == 403
    assert local.status_code == 200
    assert f'"version":"{server.__version__}"' in local.text
