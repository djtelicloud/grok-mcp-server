"""GitHub device-flow sign-in for the forge surface.

Real GitHub auth with loopback-safe properties: only the public client id
ships (no client secret), no password or GitHub token is ever stored — the
access token is used once to read the user's identity and immediately
discarded. What remains is a short-lived local session {login, tier}.

The public surface never exposes these flows (identity-free contract);
gating on ``UNIGROK_SURFACE`` happens at the route layer in server.py.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

import httpx

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
_USER_URL = "https://api.github.com/user"
_USER_AGENT = "unigrok-public-forge-auth/1"

_MAX_FLOWS = 32
_MAX_SESSIONS = 64
_SESSION_TTL_SECONDS = 12 * 3600
_VALID_TIERS = {"public", "sky", "space"}

_FLOWS: dict[str, dict[str, Any]] = {}
_SESSIONS: dict[str, dict[str, Any]] = {}

SESSION_COOKIE = "unigrok_session"


def client_id() -> str:
    return os.environ.get("UNIGROK_GITHUB_CLIENT_ID", "").strip()


def _contributor_logins() -> set[str]:
    raw = os.environ.get("UNIGROK_CONTRIBUTOR_LOGINS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _contributor_tier() -> str:
    tier = os.environ.get("UNIGROK_CONTRIBUTOR_TIER", "sky").strip().lower()
    return tier if tier in _VALID_TIERS else "sky"


def _prune(table: dict[str, dict[str, Any]], cap: int) -> None:
    now = time.monotonic()
    for key in [k for k, v in table.items() if v["expires_at"] <= now]:
        table.pop(key, None)
    while len(table) >= cap:
        table.pop(min(table, key=lambda k: table[k]["expires_at"]), None)


async def _github_post(url: str, data: dict[str, str]) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, trust_env=True
        ) as client:
            response = await client.post(
                url,
                data=data,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
        if response.status_code != 200 or len(response.content) > 16_384:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (httpx.HTTPError, TypeError, ValueError):
        return None


async def _github_user(token: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, trust_env=True
        ) as client:
            response = await client.get(
                _USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
            )
        if response.status_code != 200 or len(response.content) > 16_384:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (httpx.HTTPError, TypeError, ValueError):
        return None


async def start_flow() -> dict[str, Any]:
    """Begin a device flow. Returns the user-facing code, never device_code."""
    cid = client_id()
    if not cid:
        return {"error": "github_oauth_not_configured"}
    payload = await _github_post(
        _DEVICE_CODE_URL, {"client_id": cid, "scope": "read:user"}
    )
    if not payload or "device_code" not in payload:
        return {"error": "github_unreachable"}
    _prune(_FLOWS, _MAX_FLOWS)
    flow_id = secrets.token_urlsafe(24)
    interval = max(5, int(payload.get("interval") or 5))
    _FLOWS[flow_id] = {
        "device_code": str(payload["device_code"]),
        "interval": interval,
        "last_poll": 0.0,
        "expires_at": time.monotonic() + min(1800, int(payload.get("expires_in") or 900)),
    }
    return {
        "flow": flow_id,
        "user_code": str(payload.get("user_code") or ""),
        "verification_uri": str(
            payload.get("verification_uri") or "https://github.com/login/device"
        ),
        "interval": interval,
    }


async def poll_flow(flow_id: str) -> dict[str, Any]:
    """One bounded poll. On success creates a session; the token is discarded."""
    flow = _FLOWS.get(str(flow_id or ""))
    if not flow or flow["expires_at"] <= time.monotonic():
        _FLOWS.pop(str(flow_id or ""), None)
        return {"error": "flow_expired"}
    now = time.monotonic()
    if now - flow["last_poll"] < flow["interval"]:
        return {"status": "pending"}
    flow["last_poll"] = now
    payload = await _github_post(
        _ACCESS_TOKEN_URL,
        {
            "client_id": client_id(),
            "device_code": flow["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )
    if not payload:
        return {"error": "github_unreachable"}
    error = str(payload.get("error") or "")
    if error == "authorization_pending":
        return {"status": "pending"}
    if error == "slow_down":
        flow["interval"] = min(60, flow["interval"] + 5)
        return {"status": "pending"}
    if error:
        _FLOWS.pop(str(flow_id), None)
        return {"error": "denied" if error == "access_denied" else "flow_expired"}
    token = str(payload.get("access_token") or "")
    user = await _github_user(token) if token else None
    del token  # identity read once; the GitHub token is never stored
    if not user or not user.get("login"):
        return {"error": "github_unreachable"}
    _FLOWS.pop(str(flow_id), None)
    _prune(_SESSIONS, _MAX_SESSIONS)
    login = str(user["login"])
    tier = _contributor_tier() if login.lower() in _contributor_logins() else "public"
    sid = secrets.token_urlsafe(32)
    _SESSIONS[sid] = {
        "login": login,
        "gh_id": int(user.get("id") or 0),
        "tier": tier,
        "expires_at": time.monotonic() + _SESSION_TTL_SECONDS,
    }
    return {"session": sid, "login": login, "tier": tier}


def session_info(sid: str | None) -> dict[str, Any] | None:
    if not sid:
        return None
    session = _SESSIONS.get(sid)
    if not session or session["expires_at"] <= time.monotonic():
        _SESSIONS.pop(sid or "", None)
        return None
    return {"login": session["login"], "tier": session["tier"], "kind": "github"}


def end_session(sid: str | None) -> None:
    _SESSIONS.pop(sid or "", None)
