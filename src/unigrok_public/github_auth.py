"""GitHub device-flow sign-in for the forge surface.

Real GitHub auth with loopback-safe properties: only the public client id
ships (no client secret), no password or GitHub token is ever stored — the
access token is used once to read the user's identity and immediately
discarded. What remains is a short-lived local session {login, tier}.

The public surface never exposes these flows (identity-free contract);
gating on ``UNIGROK_SURFACE`` happens at the route layer in server.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
_USER_URL = "https://api.github.com/user"
_USER_AGENT = "unigrok-public-forge-auth/1"
_CONTROL_ORIGIN = "https://control.grokmcp.org"
_CONTROL_REGISTER_URL = f"{_CONTROL_ORIGIN}/oauth/register"
_CONTROL_AUTHORIZE_URL = f"{_CONTROL_ORIGIN}/oauth/authorize"
_CONTROL_TOKEN_URL = f"{_CONTROL_ORIGIN}/oauth/token"
_CONTROL_INTROSPECT_URL = f"{_CONTROL_ORIGIN}/oauth/introspect"
_CONTROL_SCOPE = "unigrok:connect"

_MAX_FLOWS = 32
_MAX_CONTROL_FLOWS = 32
_MAX_CONTROL_SESSIONS = 64
_SESSION_TTL_SECONDS = 12 * 3600
_CONTROL_FLOW_TTL_SECONDS = 10 * 60
_CONTROL_RECHECK_SECONDS = 60
_VALID_TIERS = {"public", "sky", "space"}
_SESSION_VERSION = 1
_SESSION_SECRET_MIN_BYTES = 32
_SESSION_SECRET_MAX_BYTES = 4096
_SESSION_KEY_BASENAME = ".unigrok-forge-session-key"
_CONTROL_LINK_BASENAME = ".unigrok-control-session"

_FLOWS: dict[str, dict[str, Any]] = {}
_CONTROL_FLOWS: dict[str, dict[str, Any]] = {}
_CONTROL_SESSION_CACHE: dict[str, dict[str, Any]] = {}
_SESSION_SECRET_CACHE: bytes | None = None

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


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _session_key_path() -> Path:
    explicit = os.environ.get("UNIGROK_FORGE_SESSION_KEY_PATH", "").strip()
    if explicit:
        return Path(explicit)
    state_path = Path(
        os.environ.get(
            "UNIGROK_STATE_PATH",
            "~/.local/share/unigrok/public.db",
        )
    ).expanduser()
    return state_path.parent / _SESSION_KEY_BASENAME


def _control_token_path() -> Path:
    explicit = os.environ.get("UNIGROK_CONTROL_TOKEN_PATH", "").strip()
    if explicit:
        return Path(explicit)
    state_path = Path(
        os.environ.get(
            "UNIGROK_STATE_PATH",
            "~/.local/share/unigrok/public.db",
        )
    ).expanduser()
    return state_path.parent / _CONTROL_LINK_BASENAME


def _read_session_secret(path: Path) -> bytes:
    if path.is_symlink():
        raise RuntimeError("forge session key path must not be a symlink")
    value = path.read_bytes()
    if not (_SESSION_SECRET_MIN_BYTES <= len(value) <= _SESSION_SECRET_MAX_BYTES):
        raise RuntimeError("forge session key has an invalid length")
    return value


def _create_session_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = secrets.token_urlsafe(48).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value


def _session_secret() -> bytes:
    global _SESSION_SECRET_CACHE
    if _SESSION_SECRET_CACHE is not None:
        return _SESSION_SECRET_CACHE
    configured = os.environ.get("UNIGROK_FORGE_SESSION_SECRET", "").encode("utf-8")
    if configured:
        if not (
            _SESSION_SECRET_MIN_BYTES
            <= len(configured)
            <= _SESSION_SECRET_MAX_BYTES
        ):
            raise RuntimeError("UNIGROK_FORGE_SESSION_SECRET has an invalid length")
        _SESSION_SECRET_CACHE = configured
        return configured
    path = _session_key_path()
    try:
        value = _read_session_secret(path)
    except FileNotFoundError:
        try:
            value = _create_session_secret(path)
        except FileExistsError:
            value = _read_session_secret(path)
    _SESSION_SECRET_CACHE = value
    return value


def store_control_token(token: str) -> None:
    """Persist one scoped UniGrok token in the Forge-owned state volume."""
    if _control_token_claims(token) is None:
        raise RuntimeError("invalid Control session token")
    path = _control_token_path()
    if path.is_symlink():
        raise RuntimeError("Control token path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.write(descriptor, token.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_control_token() -> str | None:
    path = _control_token_path()
    try:
        if path.is_symlink():
            return None
        value = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    return value if _control_token_claims(value) is not None else None


def clear_control_token() -> None:
    path = _control_token_path()
    try:
        if path.is_symlink():
            return
        path.unlink()
    except FileNotFoundError:
        pass


def _encode_session(login: str, github_id: int, *, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "exp": issued_at + _SESSION_TTL_SECONDS,
        "github_id": int(github_id),
        "iat": issued_at,
        "login": login,
        "v": _SESSION_VERSION,
    }
    encoded = _base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _base64url_encode(
        hmac.new(_session_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def _decode_session(value: str | None, *, now: int | None = None) -> dict[str, Any] | None:
    if not value or len(value) > 4096:
        return None
    try:
        encoded, signature = value.split(".", 1)
        expected = hmac.new(
            _session_secret(), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_base64url_decode(signature), expected):
            return None
        payload = json.loads(_base64url_decode(encoded).decode("utf-8"))
    except (
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None
    current = int(time.time() if now is None else now)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"exp", "github_id", "iat", "login", "v"}
        or payload.get("v") != _SESSION_VERSION
        or not isinstance(payload.get("github_id"), int)
        or payload["github_id"] <= 0
        or not isinstance(payload.get("login"), str)
        or not payload["login"]
        or len(payload["login"]) > 39
        or not isinstance(payload.get("iat"), int)
        or not isinstance(payload.get("exp"), int)
        or payload["iat"] > current + 60
        or payload["exp"] <= current
        or payload["exp"] - payload["iat"] != _SESSION_TTL_SECONDS
    ):
        return None
    return payload


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


async def _control_post(
    url: str,
    *,
    data: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any] | None:
    if url not in {
        _CONTROL_REGISTER_URL,
        _CONTROL_TOKEN_URL,
        _CONTROL_INTROSPECT_URL,
    }:
        return None
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, trust_env=True
        ) as client:
            response = await client.post(
                url,
                data=data,
                json=json_body,
                headers=headers,
            )
        if response.status_code not in {200, 201} or len(response.content) > 32_768:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (httpx.HTTPError, TypeError, ValueError):
        return None


def _valid_loopback_callback(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and port is not None
        and 1 <= port <= 65535
        and parsed.path == "/auth/control/callback"
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _control_token_claims(token: str) -> dict[str, Any] | None:
    if not token.startswith("ugtoken.") or len(token) > 8192:
        return None
    signed = token.removeprefix("ugtoken.")
    try:
        encoded, _signature = signed.split(".", 1)
        payload = json.loads(_base64url_decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != "user"
        or not isinstance(payload.get("githubId"), int)
        or payload["githubId"] <= 0
        or not isinstance(payload.get("githubLogin"), str)
        or not payload["githubLogin"]
        or len(payload["githubLogin"]) > 39
        or not isinstance(payload.get("sub"), str)
        or not isinstance(payload.get("scope"), list)
        or _CONTROL_SCOPE not in payload["scope"]
    ):
        return None
    return payload


async def start_control_flow(callback_url: str) -> dict[str, Any]:
    """Reuse Control's existing OAuth registration and remembered GitHub cookie."""
    if not _valid_loopback_callback(callback_url):
        return {"error": "invalid_callback"}
    verifier = secrets.token_urlsafe(48)
    challenge = _base64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(32)
    registration = await _control_post(
        _CONTROL_REGISTER_URL,
        json_body={
            "client_name": "UniGrok Local Forge",
            "redirect_uris": [callback_url],
        },
    )
    client_id = str((registration or {}).get("client_id") or "")
    if not client_id.startswith("ugclient.") or len(client_id) > 8192:
        return {"error": "control_unreachable"}
    _prune(_CONTROL_FLOWS, _MAX_CONTROL_FLOWS)
    _CONTROL_FLOWS[state] = {
        "callback_url": callback_url,
        "client_id": client_id,
        "expires_at": time.monotonic() + _CONTROL_FLOW_TTL_SECONDS,
        "verifier": verifier,
    }
    query = urlencode(
        {
            "client_id": client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": _CONTROL_SCOPE,
            "state": state,
        }
    )
    return {"authorization_url": f"{_CONTROL_AUTHORIZE_URL}?{query}"}


async def _introspect_control_token(token: str) -> dict[str, Any] | None:
    return await _control_post(
        _CONTROL_INTROSPECT_URL,
        data={"required_scope": _CONTROL_SCOPE},
        token=token,
    )


async def finish_control_flow(state: str, code: str) -> dict[str, Any]:
    """Exchange one PKCE code and persist the scoped Control token locally."""
    flow = _CONTROL_FLOWS.pop(state, None)
    if (
        not flow
        or flow["expires_at"] <= time.monotonic()
        or not code.startswith("ugcode.")
        or len(code) > 8192
    ):
        return {"error": "flow_expired"}
    token_payload = await _control_post(
        _CONTROL_TOKEN_URL,
        data={
            "client_id": flow["client_id"],
            "code": code,
            "code_verifier": flow["verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": flow["callback_url"],
        },
    )
    access_token = str((token_payload or {}).get("access_token") or "")
    claims = _control_token_claims(access_token)
    introspection = (
        await _introspect_control_token(access_token) if claims is not None else None
    )
    if (
        not claims
        or not introspection
        or introspection.get("active") is not True
        or introspection.get("sub") != claims.get("sub")
    ):
        return {"error": "authorization_denied"}
    login = str(claims["githubLogin"])
    try:
        store_control_token(access_token)
    except (OSError, RuntimeError):
        return {"error": "session_unavailable"}
    return {
        "login": login,
        "tier": _contributor_tier(),
    }


async def control_session_info(token: str | None) -> dict[str, Any] | None:
    """Re-check a Cloud-linked session at most once per minute."""
    current_token = token or load_control_token() or ""
    claims = _control_token_claims(current_token)
    if not claims:
        return None
    digest = hashlib.sha256(current_token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    cached = _CONTROL_SESSION_CACHE.get(digest)
    if cached and cached["expires_at"] > now:
        return dict(cached["session"])
    introspection = await _introspect_control_token(current_token)
    if introspection is None:
        return {"error": "authorization_unavailable"}
    if (
        introspection.get("active") is not True
        or introspection.get("sub") != claims.get("sub")
    ):
        _CONTROL_SESSION_CACHE.pop(digest, None)
        clear_control_token()
        return None
    _prune(_CONTROL_SESSION_CACHE, _MAX_CONTROL_SESSIONS)
    session = {
        "kind": "control",
        "login": str(claims["githubLogin"]),
        "tier": _contributor_tier(),
    }
    _CONTROL_SESSION_CACHE[digest] = {
        "expires_at": now + _CONTROL_RECHECK_SECONDS,
        "session": session,
    }
    return dict(session)


def end_control_session(token: str | None) -> None:
    current_token = token or load_control_token()
    if current_token:
        _CONTROL_SESSION_CACHE.pop(
            hashlib.sha256(current_token.encode("utf-8")).hexdigest(),
            None,
        )
    clear_control_token()


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
    login = str(user["login"])
    tier = _contributor_tier() if login.lower() in _contributor_logins() else "public"
    github_id = int(user.get("id") or 0)
    if github_id <= 0:
        return {"error": "github_unreachable"}
    try:
        session = _encode_session(login, github_id)
    except (OSError, RuntimeError):
        return {"error": "session_unavailable"}
    return {"session": session, "login": login, "tier": tier}


def session_info(sid: str | None) -> dict[str, Any] | None:
    session = _decode_session(sid)
    if not session:
        return None
    login = str(session["login"])
    tier = _contributor_tier() if login.lower() in _contributor_logins() else "public"
    return {"login": login, "tier": tier, "kind": "github"}


def end_session(sid: str | None) -> None:
    # Sessions are self-contained signed cookies. Logout deletes the cookie at
    # the route layer; there is no server-side bearer or GitHub token to revoke.
    _ = sid
