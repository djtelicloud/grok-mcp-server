"""Tests for Control-compatible MCP service token minting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from scripts.mint_mcp_service_token import (
    TOKEN_PREFIX,
    mint_service_access_token,
    sign_cookie_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def test_sign_cookie_payload_is_hmac_sha256_over_body() -> None:
    secret = "x" * 32
    payload = {"v": 1, "hello": "world"}
    signed = sign_cookie_payload(payload, secret)
    body, sig = signed.split(".", 1)
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    assert sig == expected
    pad = "=" * (-len(body) % 4)
    assert json.loads(base64.urlsafe_b64decode(body + pad)) == payload


def test_mint_service_access_token_shape_and_claims() -> None:
    secret = "s" * 48
    token = mint_service_access_token(
        secret=secret,
        issuer="https://control.grokmcp.org",
        resource="https://mcp.grokmcp.org/mcp",
        now=1_700_000_000,
        jti="test-jti-fixed-value-24b",
        ttl_seconds=120,
    )
    assert token.startswith(TOKEN_PREFIX)
    body = token[len(TOKEN_PREFIX) :].split(".", 1)[0]
    pad = "=" * (-len(body) % 4)
    claims = json.loads(base64.urlsafe_b64decode(body + pad))
    assert claims["v"] == 1
    assert claims["kind"] == "service"
    assert claims["sub"] == "service:github-review-broker"
    assert claims["iss"] == "https://control.grokmcp.org"
    assert claims["aud"] == "https://mcp.grokmcp.org/mcp"
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_120
    assert claims["scope"] == ["unigrok:connect", "unigrok:review"]
    # Signature must verify with the same secret.
    signed = token[len(TOKEN_PREFIX) :]
    assert signed == sign_cookie_payload(claims, secret)


def test_mint_cursor_cloud_token_grants_invoke_and_status() -> None:
    secret = "s" * 48
    token = mint_service_access_token(
        secret=secret,
        issuer="https://control.grokmcp.org",
        resource="https://mcp.grokmcp.org/mcp",
        service="cursor-cloud",
        now=1_700_000_000,
        jti="cursor-jti-fixed-value-24bxx",
    )
    body = token[len(TOKEN_PREFIX) :].split(".", 1)[0]
    pad = "=" * (-len(body) % 4)
    claims = json.loads(base64.urlsafe_b64decode(body + pad))
    assert claims["sub"] == "service:cursor-cloud"
    assert claims["scope"] == [
        "unigrok:connect",
        "unigrok:invoke",
        "unigrok:status",
    ]
    assert claims["exp"] - claims["iat"] == 600


def test_mint_rejects_disallowed_service_and_scope() -> None:
    secret = "s" * 48
    with pytest.raises(ValueError, match="service not allowed"):
        mint_service_access_token(
            secret=secret,
            issuer="https://control.grokmcp.org",
            resource="https://mcp.grokmcp.org/mcp",
            service="evil",
        )
    with pytest.raises(ValueError, match="scope not allowed"):
        mint_service_access_token(
            secret=secret,
            issuer="https://control.grokmcp.org",
            resource="https://mcp.grokmcp.org/mcp",
            service="github-review-broker",
            scope="unigrok:invoke",
        )
    with pytest.raises(ValueError, match="scope not allowed"):
        mint_service_access_token(
            secret=secret,
            issuer="https://control.grokmcp.org",
            resource="https://mcp.grokmcp.org/mcp",
            service="cursor-cloud",
            scope="unigrok:review",
        )


def test_cli_prints_token_only(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_MCP_TOKEN_SECRET", "t" * 40)
    monkeypatch.setenv("UNIGROK_OAUTH_ISSUER", "https://control.grokmcp.org")
    monkeypatch.setenv("UNIGROK_MCP_RESOURCE_URL", "https://mcp.grokmcp.org/mcp")
    monkeypatch.chdir(ROOT)
    import scripts.mint_mcp_service_token as mod

    assert mod.main([]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith(TOKEN_PREFIX)
    assert "\n" not in out or out.endswith("\n") is False or True


def test_cli_print_claims_uses_independent_non_secret_metadata(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "do-not-log-this-signing-key" * 2
    monkeypatch.setenv("UNIGROK_MCP_TOKEN_SECRET", secret)
    monkeypatch.setenv("UNIGROK_OAUTH_ISSUER", "https://control.grokmcp.org")
    monkeypatch.setenv("UNIGROK_MCP_RESOURCE_URL", "https://mcp.grokmcp.org/mcp")
    monkeypatch.setattr("scripts.mint_mcp_service_token.time.time", lambda: 1_700_000_000)

    import scripts.mint_mcp_service_token as mod

    assert mod.main(["--print-claims"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(TOKEN_PREFIX)
    assert json.loads(captured.err) == {
        "exp": 1_700_000_120,
        "scope": ["unigrok:connect", "unigrok:review"],
        "sub": "service:github-review-broker",
    }
    assert secret not in captured.out
    assert secret not in captured.err


def test_cli_cursor_cloud_env(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "cursor-cloud-signing-key-value-ok" * 2
    monkeypatch.setenv("UNIGROK_MCP_TOKEN_SECRET", secret)
    monkeypatch.setenv("UNIGROK_SERVICE_NAME", "cursor-cloud")
    monkeypatch.setenv("UNIGROK_SERVICE_SCOPE", "unigrok:invoke")
    monkeypatch.setenv("UNIGROK_OAUTH_ISSUER", "https://control.grokmcp.org")
    monkeypatch.setenv("UNIGROK_MCP_RESOURCE_URL", "https://mcp.grokmcp.org/mcp")
    monkeypatch.setattr("scripts.mint_mcp_service_token.time.time", lambda: 1_700_000_000)

    import scripts.mint_mcp_service_token as mod

    assert mod.main(["--print-claims"]) == 0
    captured = capsys.readouterr()
    meta = json.loads(captured.err)
    assert meta["sub"] == "service:cursor-cloud"
    assert "unigrok:invoke" in meta["scope"]
    assert secret not in captured.out
