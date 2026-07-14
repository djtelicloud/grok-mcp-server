"""Transport security allowlist for public / hosted MCP hosts."""

from __future__ import annotations

from src.http_server import public_mcp_transport_security


def test_public_mcp_transport_security_includes_localhost() -> None:
    settings = public_mcp_transport_security(public_mcp_url="")
    assert settings.enable_dns_rebinding_protection is True
    assert "localhost:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts


def test_public_mcp_transport_security_includes_public_hostname() -> None:
    settings = public_mcp_transport_security(
        public_mcp_url="https://mcp.grokmcp.org/mcp",
    )
    assert "mcp.grokmcp.org" in settings.allowed_hosts
    assert "https://mcp.grokmcp.org" in settings.allowed_origins
