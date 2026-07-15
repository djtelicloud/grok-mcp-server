#!/usr/bin/env python3
"""Mint a short-lived UniGrok MCP service access token (Control-compatible).

Matches Control Center ``createServiceAccessToken``:
HMAC-SHA256 over base64url(JSON claims), token prefix ``ugtoken.``.

Production remote MCP validates via Control introspection — never install a
long-lived static client key on Cloud Run. This script only produces short-lived
tokens for GitHub Actions, Cursor Cloud agents, and ops smoke.

Environment (required):
  UNIGROK_MCP_TOKEN_SECRET  — same value as Control ``MCP_TOKEN_SECRET``
  UNIGROK_OAUTH_ISSUER      — e.g. https://control.grokmcp.org
  UNIGROK_MCP_RESOURCE_URL  — e.g. https://mcp.grokmcp.org/mcp

Optional:
  UNIGROK_SERVICE_NAME      — github-review-broker | cursor-cloud
  UNIGROK_SERVICE_SCOPE     — unigrok:review (broker) | unigrok:invoke (cursor-cloud)
  UNIGROK_TOKEN_TTL_SECONDS — default per service (max 600)

Prints the bearer token to stdout only (no logs of the secret).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import time
from typing import Any, Mapping, Optional

from cryptography.hazmat.primitives import hashes, hmac

TOKEN_PREFIX = "ugtoken."
MAX_TTL = 600

# service → primary capability scope (connect is always included)
SERVICE_SPECS: dict[str, dict[str, Any]] = {
    "github-review-broker": {
        "scopes": frozenset({"unigrok:review"}),
        "default_scope": "unigrok:review",
        # Hosted review allows a 9-minute model read. Keep one bounded token
        # valid for that stream instead of expiring mid-response.
        "default_ttl": 600,
        "max_ttl": 600,
    },
    # Cursor Cloud / headless IDE agents: invoke + status (discover/status tools).
    "cursor-cloud": {
        # Status is granted only inside the fixed cursor-cloud bundle below.
        "scopes": frozenset({"unigrok:invoke"}),
        "default_scope": "unigrok:invoke",
        "default_ttl": 600,
        "max_ttl": 600,
    },
}


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
    signer = hmac.HMAC(secret.encode("utf-8"), hashes.SHA256())
    signer.update(body.encode("utf-8"))
    signature = signer.finalize()
    return f"{body}.{_b64url(signature)}"


def _service_token_parameters(
    service: str,
    scope: Optional[str],
    ttl_seconds: Optional[int],
) -> tuple[str, int, list[str]]:
    """Validate a service request and return its exact scope/TTL bundle."""
    if service not in SERVICE_SPECS:
        raise ValueError(f"service not allowed: {service}")
    spec = SERVICE_SPECS[service]
    primary = (scope or spec["default_scope"]).strip()
    if primary not in spec["scopes"]:
        raise ValueError(f"scope not allowed for service {service}: {primary}")
    ttl = int(ttl_seconds if ttl_seconds is not None else spec["default_ttl"])
    max_ttl = int(spec["max_ttl"])
    if not (1 <= ttl <= min(max_ttl, MAX_TTL)):
        raise ValueError(f"ttl_seconds must be 1..{min(max_ttl, MAX_TTL)} for {service}")
    if service == "cursor-cloud":
        granted = ["unigrok:connect", "unigrok:invoke", "unigrok:status"]
    else:
        granted = ["unigrok:connect", primary]
    return primary, ttl, granted


def _service_access_claims(
    *,
    issuer: str,
    resource: str,
    service: str = "github-review-broker",
    scope: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> dict[str, Any]:
    _primary, ttl, granted = _service_token_parameters(service, scope, ttl_seconds)
    if not issuer.startswith("https://") or issuer.endswith("/"):
        raise ValueError("issuer must be an https origin without trailing slash")
    if not resource.startswith("https://") or not resource.endswith("/mcp"):
        raise ValueError("resource must be https://…/mcp")
    iat = int(now if now is not None else time.time())
    return {
        "aud": resource,
        "exp": iat + ttl,
        "iat": iat,
        "iss": issuer,
        "jti": jti or _b64url(secrets.token_bytes(24)),
        "kind": "service",
        "scope": granted,
        "sub": f"service:{service}",
        "v": 1,
    }


def _mint_service_access_token_with_claims(
    *,
    secret: str,
    issuer: str,
    resource: str,
    service: str = "github-review-broker",
    scope: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    claims = _service_access_claims(
        issuer=issuer,
        resource=resource,
        service=service,
        scope=scope,
        ttl_seconds=ttl_seconds,
        now=now,
        jti=jti,
    )
    token = f"{TOKEN_PREFIX}{sign_cookie_payload(claims, secret)}"
    return token, claims


def mint_service_access_token(
    *,
    secret: str,
    issuer: str,
    resource: str,
    service: str = "github-review-broker",
    scope: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> str:
    token, _claims = _mint_service_access_token_with_claims(
        secret=secret,
        issuer=issuer,
        resource=resource,
        service=service,
        scope=scope,
        ttl_seconds=ttl_seconds,
        now=now,
        jti=jti,
    )
    return token


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
    scope = os.environ.get("UNIGROK_SERVICE_SCOPE", "").strip() or None
    raw_ttl = os.environ.get("UNIGROK_TOKEN_TTL_SECONDS", "").strip()
    try:
        ttl = int(raw_ttl) if raw_ttl else None
    except ValueError as exc:
        raise SystemExit("UNIGROK_TOKEN_TTL_SECONDS must be an integer") from exc

    token, claims = _mint_service_access_token_with_claims(
        secret=secret,
        issuer=issuer,
        resource=resource,
        service=service,
        scope=scope,
        ttl_seconds=ttl,
    )
    if args.print_claims:
        claims_metadata = {
            "exp": claims["exp"],
            "scope": claims["scope"],
            "sub": claims["sub"],
        }
        print(json.dumps(claims_metadata, sort_keys=True), file=sys.stderr)
    output = token.encode("ascii")
    if sys.stdout.isatty():
        output += b"\n"
    os.write(sys.stdout.fileno(), output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
