"""Detect whether the active caller is an official GitHub contributor.

Public-safe design:
- Never trust ``X-Client-ID`` alone (telemetry label, not authentication).
- Prefer server-side verification: allowlist and/or GitHub API with a **service** token.
- Expose only a boolean affiliation view to the caller; do not leak the full roster.
- Local single-operator installs can set an allowlist or optional login bind.

Config (service environment):

- ``UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST`` — comma/space-separated GitHub logins
- ``UNIGROK_GITHUB_CONTRIBUTOR_REPOS`` — ``owner/repo`` list to check collaborator status
- ``UNIGROK_GITHUB_CONTRIBUTOR_ORGS`` — org logins; membership = official
- ``UNIGROK_GITHUB_TOKEN`` / ``GITHUB_TOKEN`` — service token for API checks (never client-supplied)
- ``UNIGROK_GITHUB_AFFILIATION_CACHE_SECONDS`` — cache TTL (default 300)
- ``UNIGROK_GITHUB_LOGIN`` — optional **operator-configured** login for local:operator binds
  (not a client spoof header)

Optional request claim path: OAuth introspection may supply ``login`` /
``preferred_username`` when the issuer is GitHub-shaped (wired by remote_auth).
"""

from __future__ import annotations

import os
import re
import time
from contextvars import ContextVar
from typing import Any

import httpx

from .identity import get_active_principal, principal_kind

_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_CACHE: dict[str, tuple[float, bool]] = {}

# Request-scoped GitHub login claim (set by auth middleware / remote_auth).
_REQUEST_GITHUB_LOGIN: ContextVar[str | None] = ContextVar(
    "unigrok_request_github_login", default=None
)


def set_request_github_login(login: str | None) -> None:
    _REQUEST_GITHUB_LOGIN.set(_normalize_login(login))


def clear_request_github_login() -> None:
    _REQUEST_GITHUB_LOGIN.set(None)


def _normalize_login(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    login = raw.strip().lstrip("@")
    if not login or len(login) > 39:
        return None
    if not _LOGIN_RE.match(login):
        return None
    return login.lower()


def _split_csv(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    parts: list[str] = []
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        for bit in chunk.split():
            bit = bit.strip()
            if bit:
                parts.append(bit)
    return parts


def configured_allowlist() -> set[str]:
    out: set[str] = set()
    for item in _split_csv("UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST"):
        login = _normalize_login(item)
        if login:
            out.add(login)
    return out


def configured_repos() -> list[str]:
    repos: list[str] = []
    for item in _split_csv("UNIGROK_GITHUB_CONTRIBUTOR_REPOS"):
        if item.count("/") == 1:
            owner, repo = item.split("/", 1)
            if owner and repo and ".." not in owner and ".." not in repo:
                repos.append(f"{owner}/{repo}")
    return repos


def configured_orgs() -> list[str]:
    orgs: list[str] = []
    for item in _split_csv("UNIGROK_GITHUB_CONTRIBUTOR_ORGS"):
        login = _normalize_login(item)
        if login:
            orgs.append(login)
    return orgs


def service_github_token() -> str | None:
    for key in ("UNIGROK_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value and len(value) <= 8_192:
            return value
    return None


def cache_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("UNIGROK_GITHUB_AFFILIATION_CACHE_SECONDS", "300"))
    except (TypeError, ValueError):
        value = 300
    return max(30, min(value, 86_400))


def resolve_github_login(*, oauth_claims: dict[str, Any] | None = None) -> str | None:
    """Best-effort GitHub login for the current request (may be None)."""
    bound = _REQUEST_GITHUB_LOGIN.get()
    if bound:
        return bound
    if oauth_claims:
        for key in ("login", "preferred_username", "nickname", "name"):
            login = _normalize_login(oauth_claims.get(key))
            if login:
                return login
        # Some GitHub App tokens put login under nested user objects
        user = oauth_claims.get("user")
        if isinstance(user, dict):
            login = _normalize_login(user.get("login"))
            if login:
                return login
    # Local operator bind (service config only — not a client header)
    principal = get_active_principal()
    if principal in (None, "local:operator") or principal_kind() == "none":
        return _normalize_login(os.environ.get("UNIGROK_GITHUB_LOGIN"))
    return None


def _cache_get(login: str) -> bool | None:
    hit = _CACHE.get(login)
    if not hit:
        return None
    expires, value = hit
    if time.monotonic() >= expires:
        _CACHE.pop(login, None)
        return None
    return value


def _cache_set(login: str, value: bool) -> None:
    _CACHE[login] = (time.monotonic() + float(cache_ttl_seconds()), value)


async def _github_api_get(path: str, token: str) -> tuple[int, Any]:
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "unigrok-public-affiliation/1",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=headers)
    except (httpx.HTTPError, TypeError, ValueError):
        return 0, None
    try:
        body = response.json() if response.content else None
    except (TypeError, ValueError):
        body = None
    return response.status_code, body


async def _api_is_official(login: str, token: str) -> bool | None:
    """Return True/False if API decides, None if undetermined (network/config)."""
    for org in configured_orgs():
        status, _ = await _github_api_get(f"/orgs/{org}/members/{login}", token)
        if status == 204:
            return True
        if status == 404:
            continue
        if status in (401, 403):
            return None
    for repo in configured_repos():
        status, _ = await _github_api_get(
            f"/repos/{repo}/collaborators/{login}", token
        )
        if status == 204:
            return True
        if status == 404:
            continue
        if status in (401, 403):
            return None
    # If we had orgs/repos configured and all 404 → not a collaborator
    if configured_orgs() or configured_repos():
        return False
    return None


async def is_official_contributor(
    login: str | None = None,
    *,
    oauth_claims: dict[str, Any] | None = None,
) -> tuple[bool | None, str]:
    """Return (is_official, source).

    is_official:
      True  — verified official
      False — verified not official (when roster sources exist)
      None  — unknown (no login / no config / API unavailable)
    """
    resolved = login or resolve_github_login(oauth_claims=oauth_claims)
    if not resolved:
        return None, "no_github_login"

    allow = configured_allowlist()
    if resolved in allow:
        return True, "allowlist"

    cached = _cache_get(resolved)
    if cached is not None:
        return cached, "cache"

    token = service_github_token()
    if not token:
        if allow:
            # Allowlist configured but login not on it
            return False, "allowlist_miss"
        if not configured_repos() and not configured_orgs():
            return None, "not_configured"
        return None, "no_service_token"

    decided = await _api_is_official(resolved, token)
    if decided is None:
        return None, "api_unavailable"
    _cache_set(resolved, decided)
    return decided, "github_api"


async def affiliation_public_view(
    *,
    oauth_claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safe dict for status/discover — no full roster, no tokens."""
    login = resolve_github_login(oauth_claims=oauth_claims)
    is_official, source = await is_official_contributor(
        login, oauth_claims=oauth_claims
    )
    repos = configured_repos()
    orgs = configured_orgs()
    allow = configured_allowlist()
    return {
        "github_login_detected": bool(login),
        # Do not echo raw login to shared logs; optional short hash for self-debug
        "github_login_hint": (login[:2] + "***") if login and len(login) > 2 else None,
        "is_official_contributor": is_official,
        "source": source,
        "check_configured": bool(
            allow
            or repos
            or orgs
            or service_github_token()
            or os.environ.get("UNIGROK_GITHUB_LOGIN", "").strip()
        ),
        # Counts only — never echo private repo/org names to public status surfaces
        "allowlist_entries": len(allow),
        "repo_checks": len(repos),
        "org_checks": len(orgs),
        "notes": (
            "X-Client-ID is never used for affiliation. "
            "Configure allowlist and/or GitHub API token + repos/orgs. "
            "Local operators may set UNIGROK_GITHUB_LOGIN + allowlist."
        ),
    }


def clear_cache() -> None:
    _CACHE.clear()
