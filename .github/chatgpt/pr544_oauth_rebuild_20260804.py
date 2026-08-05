from __future__ import annotations

import ast
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
path = repo / "src/unigrok_public/remote_auth.py"
text = path.read_text(encoding="utf-8")

if "import asyncio\n" not in text:
    marker = "from __future__ import annotations\n\n"
    if marker not in text:
        raise RuntimeError("future import marker not found")
    text = text.replace(marker, marker + "import asyncio\n", 1)

text = text.replace(
    "if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):",
    "if not value or any(\n        ord(char) <= 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF\n        for char in value\n    ):",
    1,
)
text = text.replace(
    "    except ValueError:\n        return None\n",
    "    except (ValueError, UnicodeError):\n        return None\n",
    1,
)

module = ast.parse(text)
functions = {
    node.name: node
    for node in module.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
canonical = functions.get("canonical_oauth_principal")
introspect = functions.get("introspect_oauth_token")
if canonical is None or introspect is None:
    raise RuntimeError("required auth functions not found")

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
    """The OAuth control plane could not make an authorization decision."""


_oauth_introspection_flights: dict[
    tuple[asyncio.AbstractEventLoop, bytes, str],
    asyncio.Task[dict[str, Any] | None],
] = {}


def _oauth_control_unavailable(detail: str) -> OAuthIntrospectionUnavailable:
    return OAuthIntrospectionUnavailable(detail)


async def _introspect_oauth_token_once(
    token: str, required: str
) -> dict[str, Any] | None:
    url = introspection_url()
    if not url or not token or len(token) > 8_192:
        return None
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
    except (httpx.HTTPError, TypeError, ValueError, UnicodeError) as exc:
        raise _oauth_control_unavailable("oauth_introspection_transport_failure") from exc
    if response.status_code != 200:
        raise _oauth_control_unavailable("oauth_introspection_http_failure")
    if len(response.content) > 16_384:
        raise _oauth_control_unavailable("oauth_introspection_response_too_large")
    try:
        payload = response.json()
    except (TypeError, ValueError, UnicodeError) as exc:
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
    token: str, required: str
) -> dict[str, Any] | None:
    try:
        return await _introspect_oauth_token_once(token, required)
    except asyncio.CancelledError as exc:
        raise _oauth_control_unavailable("oauth_introspection_cancelled") from exc


def _finish_oauth_introspection_flight(
    key: tuple[asyncio.AbstractEventLoop, bytes, str],
    task: asyncio.Task[dict[str, Any] | None],
) -> None:
    if _oauth_introspection_flights.get(key) is task:
        _oauth_introspection_flights.pop(key, None)
    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        return


async def introspect_oauth_token(token: str, required: str) -> dict[str, Any] | None:
    if not introspection_url() or not token or len(token) > 8_192:
        return None
    loop = asyncio.get_running_loop()
    key = (loop, _digest_token(token), required)
    task = _oauth_introspection_flights.get(key)
    if task is None:
        task = loop.create_task(_run_oauth_introspection(token, required))
        _oauth_introspection_flights[key] = task
        task.add_done_callback(
            lambda completed, flight_key=key: _finish_oauth_introspection_flight(
                flight_key, completed
            )
        )
    return await asyncio.shield(task)
'''

lines = text.splitlines(keepends=True)
for start, end, block in sorted(
    [
        (canonical.lineno - 1, canonical.end_lineno, canonical_block + "\n"),
        (introspect.lineno - 1, introspect.end_lineno, introspection_block + "\n"),
    ],
    reverse=True,
):
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
parent: dict[ast.AST, ast.AST] = {}
for node in ast.walk(remote_class):
    for child in ast.iter_child_nodes(node):
        parent[child] = node
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
    while current in parent and not isinstance(current, ast.stmt):
        current = parent[current]
    if not isinstance(current, ast.stmt):
        raise RuntimeError("introspection call statement not found")
    statements[(current.lineno, current.end_lineno)] = current
if not statements:
    raise RuntimeError("middleware introspection calls not found")

lines = text.splitlines(keepends=True)
for start_end in sorted(statements, reverse=True):
    start_line, end_line = start_end
    start = start_line - 1
    end = end_line
    original = "".join(lines[start:end])
    indent = original[: len(original) - len(original.lstrip(" "))]
    indented_original = "".join(
        "    " + line if line.strip() else line
        for line in original.splitlines(keepends=True)
    )
    wrapped = (
        f"{indent}try:\n"
        f"{indented_original}"
        f"{indent}except OAuthIntrospectionUnavailable:\n"
        f"{indent}    response = JSONResponse(\n"
        f"{indent}        {{\"error\": \"temporarily_unavailable\"}},\n"
        f"{indent}        status_code=503,\n"
        f"{indent}        headers={{\"Cache-Control\": \"no-store\", \"Retry-After\": \"5\"}},\n"
        f"{indent}    )\n"
        f"{indent}    await response(scope, receive, send)\n"
        f"{indent}    return\n"
    )
    lines[start:end] = [wrapped]
text = "".join(lines)
ast.parse(text)
path.write_text(text, encoding="utf-8")

test_path = repo / "tests/test_remote_boundary.py"
test_text = test_path.read_text(encoding="utf-8")
if "import asyncio\n" not in test_text:
    marker = "from __future__ import annotations\n\n"
    test_text = test_text.replace(marker, marker + "import asyncio\n", 1)

regressions = r'''

class _OAuthTestResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def json(self) -> object:
        return self._payload


@pytest.mark.asyncio
async def test_oauth_introspection_control_outage_is_retryable(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthTestResponse:
            return _OAuthTestResponse({}, status_code=503)

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

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthTestResponse:
            return _OAuthTestResponse({"active": False})

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

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthTestResponse:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _OAuthTestResponse(payload)

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    tasks = [
        asyncio.create_task(
            remote_auth.introspect_oauth_token("shared-token", "unigrok:connect")
        )
        for _ in range(8)
    ]
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)
    assert calls == 1
    assert all(result == results[0] for result in results)
    assert results[0] is not None


@pytest.mark.asyncio
async def test_oauth_malformed_unicode_claim_is_deterministic_for_coalesced_callers(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
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

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthTestResponse:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _OAuthTestResponse(payload)

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    remote_auth._oauth_introspection_flights.clear()
    tasks = [
        asyncio.create_task(
            remote_auth.introspect_oauth_token("unicode-token", "unigrok:connect")
        )
        for _ in range(6)
    ]
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*tasks) == [None] * 6
    assert calls == 1


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

        async def post(self, *_args: Any, **_kwargs: Any) -> _OAuthTestResponse:
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
async def test_oauth_middleware_returns_503_only_for_control_unavailability(
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
'''

if "test_oauth_malformed_unicode_claim_is_deterministic_for_coalesced_callers" not in test_text:
    test_text += regressions
    test_path.write_text(test_text, encoding="utf-8")
