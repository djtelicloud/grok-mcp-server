from __future__ import annotations

import ast
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
auth_path = repo / "src/unigrok_public/remote_auth.py"
test_path = repo / "tests/test_remote_boundary.py"

text = auth_path.read_text(encoding="utf-8")
if "OAuthIntrospectionUnavailable" in text:
    raise RuntimeError("OAuth introspection patch already present")
if "import asyncio\n" not in text:
    marker = "from __future__ import annotations\n\n"
    if marker not in text:
        raise RuntimeError("future import marker not found")
    text = text.replace(marker, marker + "import asyncio\n", 1)

module = ast.parse(text)
functions = {
    node.name: node
    for node in module.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
validated_url = functions.get("_validated_https_url")
canonical = functions.get("canonical_oauth_principal")
introspect = functions.get("introspect_oauth_token")
if validated_url is None or canonical is None or introspect is None:
    raise RuntimeError("required OAuth functions not found")

validated_url_block = '''def _validated_https_url(raw: str) -> str | None:
    value = str(raw or "").strip()
    if not value or any(
        ord(char) <= 32
        or ord(char) == 127
        or 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    ):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    normalized_host = host.lower().rstrip(".")
    if normalized_host == "localhost" or normalized_host.endswith(
        (".localhost", ".local", ".internal")
    ):
        return None
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    return f"https://{parsed.netloc}{parsed.path.rstrip('/')}"
'''

canonical_block = '''def canonical_oauth_principal(issuer: Any, subject: Any) -> str | None:
    if not isinstance(issuer, str) or issuer not in authorization_servers():
        return None
    if not isinstance(subject, str) or not subject or len(subject) > 1_024:
        return None
    if any(
        ord(char) <= 31
        or ord(char) == 127
        or 0xD800 <= ord(char) <= 0xDFFF
        for char in subject
    ):
        return None
    try:
        encoded_issuer = quote(issuer, safe="-._~")
        encoded_subject = quote(subject, safe="-._~")
    except UnicodeError:
        return None
    return f"oauth:{encoded_issuer}:{encoded_subject}"
'''

introspection_block = '''class OAuthIntrospectionUnavailable(RuntimeError):
    pass


_oauth_introspection_flights: dict[
    tuple[asyncio.AbstractEventLoop, str, bytes, str],
    asyncio.Task[dict[str, Any] | None],
] = {}
_OAUTH_INTROSPECTION_MAX_FLIGHTS = 256


def _oauth_control_unavailable(detail: str) -> OAuthIntrospectionUnavailable:
    return OAuthIntrospectionUnavailable(detail)


async def _introspect_oauth_token_once(
    url: str, token: str, required: str
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "unigrok-public-remote-mcp/1",
                },
                data={"required_scope": required},
            )
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, TypeError, UnicodeError, ValueError) as exc:
        raise _oauth_control_unavailable("oauth_introspection_transport_failure") from exc
    if response.status_code != 200:
        raise _oauth_control_unavailable("oauth_introspection_http_failure")
    if len(response.content) > 16_384:
        raise _oauth_control_unavailable("oauth_introspection_response_too_large")
    try:
        payload = response.json()
    except (TypeError, UnicodeError, ValueError) as exc:
        raise _oauth_control_unavailable("oauth_introspection_invalid_json") from exc
    if not isinstance(payload, dict):
        raise _oauth_control_unavailable("oauth_introspection_invalid_payload")
    active = payload.get("active")
    if active is False:
        return None
    if active is not True:
        raise _oauth_control_unavailable("oauth_introspection_missing_active_state")
    scopes = payload.get("scope")
    granted = set(scopes.split()) if isinstance(scopes, str) else set()
    if not set(required.split()).issubset(granted):
        return None
    principal = canonical_oauth_principal(payload.get("iss"), payload.get("sub"))
    if principal is None:
        return None
    audience = payload.get("aud")
    if isinstance(audience, str):
        audiences = (audience,)
    elif (
        isinstance(audience, list)
        and len(audience) <= 16
        and all(isinstance(item, str) and len(item) <= 2_048 for item in audience)
    ):
        audiences = tuple(audience)
    else:
        return None
    if public_mcp_resource() not in audiences:
        return None
    return {
        "active": True,
        "scope": scopes,
        "unigrok_principal": principal,
        "unigrok_auth": "oauth",
    }


async def _run_oauth_introspection(
    url: str, token: str, required: str
) -> dict[str, Any] | None:
    try:
        return await _introspect_oauth_token_once(url, token, required)
    except asyncio.CancelledError as exc:
        raise _oauth_control_unavailable("oauth_introspection_cancelled") from exc


def _finish_oauth_introspection_flight(
    key: tuple[asyncio.AbstractEventLoop, str, bytes, str],
    task: asyncio.Task[dict[str, Any] | None],
) -> None:
    if _oauth_introspection_flights.get(key) is task:
        _oauth_introspection_flights.pop(key, None)
    if task.cancelled():
        return
    task.exception()


async def introspect_oauth_token(token: str, required: str) -> dict[str, Any] | None:
    if not token or len(token) > 8_192:
        return None
    url = introspection_url()
    if not url:
        raise _oauth_control_unavailable("oauth_introspection_not_configured")
    try:
        token_digest = _digest_token(token)
    except UnicodeError:
        return None
    loop = asyncio.get_running_loop()
    key = (loop, url, token_digest, required)
    task = _oauth_introspection_flights.get(key)
    if task is None:
        if len(_oauth_introspection_flights) >= _OAUTH_INTROSPECTION_MAX_FLIGHTS:
            raise _oauth_control_unavailable("oauth_introspection_capacity_exhausted")
        task = loop.create_task(_run_oauth_introspection(url, token, required))
        _oauth_introspection_flights[key] = task
        task.add_done_callback(
            lambda completed, flight_key=key: _finish_oauth_introspection_flight(
                flight_key, completed
            )
        )
    return await asyncio.shield(task)
'''

lines = text.splitlines(keepends=True)
replacements = [
    (validated_url.lineno - 1, validated_url.end_lineno, validated_url_block + "\n"),
    (canonical.lineno - 1, canonical.end_lineno, canonical_block + "\n"),
    (introspect.lineno - 1, introspect.end_lineno, introspection_block + "\n"),
]
for start, end, block in sorted(replacements, reverse=True):
    lines[start:end] = [block]
text = "".join(lines)

module = ast.parse(text)
remote_class = next(
    (
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "RemoteOAuthMiddleware"
    ),
    None,
)
if remote_class is None:
    raise RuntimeError("RemoteOAuthMiddleware not found")
parents: dict[ast.AST, ast.AST] = {}
for node in ast.walk(remote_class):
    for child in ast.iter_child_nodes(node):
        parents[child] = node
statements: dict[tuple[int, int], ast.stmt] = {}
for node in ast.walk(remote_class):
    if not isinstance(node, ast.Await):
        continue
    call = node.value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        continue
    if call.func.id != "introspect_oauth_token":
        continue
    current: ast.AST = node
    while current in parents and not isinstance(current, ast.stmt):
        current = parents[current]
    if not isinstance(current, ast.stmt):
        raise RuntimeError("introspection call statement not found")
    statements[(current.lineno, current.end_lineno)] = current
if not statements:
    raise RuntimeError("middleware introspection calls not found")

lines = text.splitlines(keepends=True)
for start_line, end_line in sorted(statements, reverse=True):
    start = start_line - 1
    original = "".join(lines[start:end_line])
    indent = original[: len(original) - len(original.lstrip(" "))]
    nested = "".join(
        "    " + line if line.strip() else line
        for line in original.splitlines(keepends=True)
    )
    wrapped = (
        f"{indent}try:\n"
        f"{nested}"
        f"{indent}except OAuthIntrospectionUnavailable:\n"
        f"{indent}    response = JSONResponse(\n"
        f"{indent}        {{\"error\": \"temporarily_unavailable\"}},\n"
        f"{indent}        status_code=503,\n"
        f"{indent}        headers={{\n"
        f"{indent}            \"Cache-Control\": \"no-store\",\n"
        f"{indent}            \"Retry-After\": \"5\",\n"
        f"{indent}        }},\n"
        f"{indent}    )\n"
        f"{indent}    await response(scope, receive, send)\n"
        f"{indent}    return\n"
    )
    lines[start:end_line] = [wrapped]
text = "".join(lines)
ast.parse(text)
auth_path.write_text(text, encoding="utf-8")

test_text = test_path.read_text(encoding="utf-8")
if "test_oauth_malformed_unicode_claim_is_deterministic_for_simultaneous_callers" in test_text:
    raise RuntimeError("OAuth regression tests already present")
if "import asyncio\n" not in test_text:
    marker = "from __future__ import annotations\n\n"
    if marker not in test_text:
        raise RuntimeError("test future import marker not found")
    test_text = test_text.replace(marker, marker + "import asyncio\n", 1)

regressions = r'''

class _OAuthControlResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def json(self) -> object:
        return self._payload


@pytest.mark.asyncio
async def test_oauth_introspection_control_http_failure_is_retryable(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthControlResponse:
            return _OAuthControlResponse({}, 503)

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    with pytest.raises(remote_auth.OAuthIntrospectionUnavailable):
        await remote_auth.introspect_oauth_token("opaque-token", "unigrok:connect")


@pytest.mark.asyncio
async def test_oauth_introspection_explicit_inactive_token_is_denied(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthControlResponse:
            return _OAuthControlResponse({"active": False})

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    assert await remote_auth.introspect_oauth_token(
        "opaque-token", "unigrok:connect"
    ) is None


@pytest.mark.asyncio
async def test_oauth_introspection_coalesces_simultaneous_callers(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    start = asyncio.Event()
    calls = 0
    payload = {
        "active": True,
        "scope": "unigrok:connect",
        "iss": AUTHORIZATION_SERVER,
        "sub": "concurrent-reviewer",
        "aud": PUBLIC_RESOURCE,
    }

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthControlResponse:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _OAuthControlResponse(payload)

    async def caller() -> dict[str, Any] | None:
        await start.wait()
        return await remote_auth.introspect_oauth_token(
            "shared-token", "unigrok:connect"
        )

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    tasks = [asyncio.create_task(caller()) for _ in range(8)]
    start.set()
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)
    assert calls == 1
    assert results[0] is not None
    assert all(result == results[0] for result in results)


@pytest.mark.asyncio
async def test_oauth_malformed_unicode_claim_is_deterministic_for_simultaneous_callers(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    start = asyncio.Event()
    calls = 0
    payload = {
        "active": True,
        "scope": "unigrok:connect",
        "iss": AUTHORIZATION_SERVER,
        "sub": "malformed-\ud800-subject",
        "aud": PUBLIC_RESOURCE,
    }

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthControlResponse:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _OAuthControlResponse(payload)

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("malformed OAuth identity reached the MCP application")

    async def caller() -> tuple[int, dict[str, str], bytes]:
        await start.wait()
        return await _asgi_exchange(
            remote_auth.RemoteOAuthMiddleware(downstream),
            path="/mcp",
            method="POST",
            body_chunks=(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',),
            headers=((b"authorization", b"Bearer unicode-token"),),
        )

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    tasks = [asyncio.create_task(caller()) for _ in range(6)]
    start.set()
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)
    assert calls == 1
    assert [status for status, _headers, _body in results] == [401] * 6
    assert [json.loads(body) for _status, _headers, body in results] == [
        {"error": "unauthorized"}
    ] * 6


@pytest.mark.asyncio
async def test_oauth_cancelled_leader_does_not_cancel_shared_outage(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthControlResponse:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            raise remote_auth.httpx.ConnectError("control unavailable")

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    leader = asyncio.create_task(
        remote_auth.introspect_oauth_token("cancel-token", "unigrok:connect")
    )
    await entered.wait()
    waiter = asyncio.create_task(
        remote_auth.introspect_oauth_token("cancel-token", "unigrok:connect")
    )
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    release.set()
    with pytest.raises(remote_auth.OAuthIntrospectionUnavailable):
        await waiter
    assert calls == 1


@pytest.mark.asyncio
async def test_oauth_middleware_returns_503_for_control_unavailability(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(_token: str, _required: str) -> dict[str, Any] | None:
        raise remote_auth.OAuthIntrospectionUnavailable("control unavailable")

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", unavailable)

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("unavailable authorization reached the MCP application")

    status, headers, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path="/mcp",
        method="POST",
        body_chunks=(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',),
        headers=((b"authorization", b"Bearer opaque-token"),),
    )
    assert status == 503
    assert json.loads(response_body) == {"error": "temporarily_unavailable"}
    assert headers["cache-control"] == "no-store"
    assert headers["retry-after"] == "5"
    assert "www-authenticate" not in headers


def test_oauth_principal_rejects_surrogate_unicode(remote_oauth_env: None) -> None:
    assert remote_auth.canonical_oauth_principal(
        AUTHORIZATION_SERVER, "malformed-\ud800-subject"
    ) is None
'''

test_text += regressions
test_path.write_text(test_text, encoding="utf-8")
ast.parse(test_text)
