#!/usr/bin/env python3
"""Mint a short-lived UniGrok MCP service access token (Control-compatible).

Matches Control Center ``createServiceAccessToken``:
HMAC-SHA256 over base64url(JSON claims), token prefix ``ugtoken.``.

Production remote MCP validates via Control introspection — never install a
long-lived static client key on Cloud Run. This script only produces ~120s
tokens for GitHub Actions / ops smoke.

Environment (required):
  UNIGROK_MCP_TOKEN_SECRET  — same value as Control ``MCP_TOKEN_SECRET``
  UNIGROK_OAUTH_ISSUER      — e.g. https://control.grokmcp.org
  UNIGROK_MCP_RESOURCE_URL  — e.g. https://mcp.grokmcp.org/mcp

Optional:
  UNIGROK_SERVICE_NAME      — default github-review-broker
  UNIGROK_SERVICE_SCOPE     — default unigrok:review
  UNIGROK_TOKEN_TTL_SECONDS — default 120 (max 600)

Prints the bearer token to stdout only (no logs of the secret).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from typing import Any, Mapping, Optional

TOKEN_PREFIX = "ugtoken."
DEFAULT_TTL = 120
MAX_TTL = 600
ALLOWED_SCOPES = frozenset(
    {
        "unigrok:connect",
        "unigrok:invoke",
        "unigrok:review",
        "unigrok:chat",
        "unigrok:status",
    }
)
ALLOWED_SERVICES = frozenset({"github-review-broker"})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def sign_cookie_payload(payload: Mapping[str, Any], secret: str) -> str:
    """Mirror Control ``signCookiePayload`` (HMAC-SHA256 over base64url body)."""
    if not (32 <= len(secret) <= 4_096):
        raise ValueError("MCP token secret length is invalid")
    body = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    if len(body) > 4_096:
        raise ValueError("cookie payload is too large")
    signature = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def mint_service_access_token(
    *,
    secret: str,
    issuer: str,
    resource: str,
    service: str = "github-review-broker",
    scope: str = "unigrok:review",
    ttl_seconds: int = DEFAULT_TTL,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> str:
    if service not in ALLOWED_SERVICES:
        raise ValueError(f"service not allowed: {service}")
    # Service mint is review-only; invoke/chat/status stay for user OAuth.
    if scope != "unigrok:review":
        raise ValueError(f"scope not allowed for service mint: {scope}")
    if not issuer.startswith("https://") or issuer.endswith("/"):
        raise ValueError("issuer must be an https origin without trailing slash")
    if not resource.startswith("https://") or not resource.endswith("/mcp"):
        raise ValueError("resource must be https://…/mcp")
    if not (1 <= int(ttl_seconds) <= MAX_TTL):
        raise ValueError(f"ttl_seconds must be 1..{MAX_TTL}")
    iat = int(now if now is not None else time.time())
    claims = {
        "aud": resource,
        "exp": iat + int(ttl_seconds),
        "iat": iat,
        "iss": issuer,
        "jti": jti or _b64url(secrets.token_bytes(24)),
        "kind": "service",
        "scope": ["unigrok:connect", scope],
        "sub": f"service:{service}",
        "v": 1,
    }
    return f"{TOKEN_PREFIX}{sign_cookie_payload(claims, secret)}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-claims",
        action="store_true",
        help="print JSON claims metadata to stderr (never the secret)",
    )
    args = parser.parse_args(argv)

    secret = _required_env("UNIGROK_MCP_TOKEN_SECRET")
    issuer = os.environ.get("UNIGROK_OAUTH_ISSUER", "https://control.grokmcp.org").strip()
    resource = os.environ.get("UNIGROK_MCP_RESOURCE_URL", "https://mcp.grokmcp.org/mcp").strip()
    service = os.environ.get("UNIGROK_SERVICE_NAME", "github-review-broker").strip()
    scope = os.environ.get("UNIGROK_SERVICE_SCOPE", "unigrok:review").strip()
    try:
        ttl = int(os.environ.get("UNIGROK_TOKEN_TTL_SECONDS", str(DEFAULT_TTL)))
    except ValueError as exc:
        raise SystemExit("UNIGROK_TOKEN_TTL_SECONDS must be an integer") from exc

    token = mint_service_access_token(
        secret=secret,
        issuer=issuer,
        resource=resource,
        service=service,
        scope=scope,
        ttl_seconds=ttl,
    )
    if args.print_claims:
        # Decode payload segment only for operator debugging (no secret).
        body = token[len(TOKEN_PREFIX) :].split(".", 1)[0]
        pad = "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body + pad))
        print(json.dumps({"sub": claims["sub"], "exp": claims["exp"], "scope": claims["scope"]}, sort_keys=True), file=sys.stderr)
    sys.stdout.write(token)
    if sys.stdout.isatty():
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
