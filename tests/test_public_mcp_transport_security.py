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
    assert settings.allowed_hosts[-1] == "mcp.grokmcp.org"
    assert settings.allowed_origins[-1] == "https://mcp.grokmcp.org"


def test_public_mcp_transport_security_rejects_non_https() -> None:
    baseline = public_mcp_transport_security(public_mcp_url="")
    settings = public_mcp_transport_security(
        public_mcp_url="http://mcp.grokmcp.org/mcp",
    )
    assert settings.allowed_hosts == baseline.allowed_hosts
    assert settings.allowed_origins == baseline.allowed_origins
    assert "mcp.grokmcp.org" not in settings.allowed_hosts


def test_public_mcp_transport_security_rejects_link_local_and_internal() -> None:
    baseline = public_mcp_transport_security(public_mcp_url="")
    for unsafe in (
        "http://169.254.169.254/mcp",
        "https://169.254.169.254/mcp",
        "https://evil.internal/mcp",
        "https://localhost/mcp",
        "https://mcp.grokmcp.org/not-mcp",
    ):
        settings = public_mcp_transport_security(public_mcp_url=unsafe)
        assert settings.allowed_hosts == baseline.allowed_hosts, unsafe
        assert "169.254.169.254" not in settings.allowed_hosts
        assert "evil.internal" not in settings.allowed_hosts
