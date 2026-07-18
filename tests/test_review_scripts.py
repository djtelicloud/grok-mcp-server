# Covers the restored hosted-review broker scripts:
# scripts/github-grok-review.py and scripts/mint_mcp_service_token.py.
import base64
import hashlib
import hmac
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

SECRET = "test-only-secret-0123456789ABCDEFGH-not-real"  # noqa: S105 - invented test value
WRONG_SECRET = "wrong-secret-value-ZZZZ-0123456789abcdefgh"  # noqa: S105 - invented test value
ISSUER = "https://control.grokmcp.org"
RESOURCE = "https://mcp.grokmcp.org/mcp"
FIXED_NOW = 1_752_700_000
FIXED_JTI = "fixed-jti-0001"
TOKEN_RE = re.compile(r"ugtoken\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
SCRIPT_ENV_VARS = (
    "INPUT_PR_NUMBER",
    "EXPECTED_HEAD_SHA",
    "EXPECTED_BASE_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_SERVER_URL",
    "UNIGROK_MCP_TOKEN_SECRET",
    "UNIGROK_CLIENT_TOKEN",
    "UNIGROK_OAUTH_ISSUER",
    "UNIGROK_MCP_RESOURCE_URL",
    "UNIGROK_MCP_URL",
    "UNIGROK_TOKEN_TTL_SECONDS",
    "UNIGROK_SERVICE_NAME",
    "UNIGROK_SERVICE_SCOPE",
)


def _load_script(module_name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mint = _load_script("mint_mcp_service_token", "mint_mcp_service_token.py")
review = _load_script("github_grok_review", "github-grok-review.py")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SCRIPT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _independent_verify(token: str, secret: str) -> dict[str, Any] | None:
    """Stdlib-only verifier built from the docstring spec, never the module signer."""
    if not token.startswith("ugtoken."):
        return None
    parts = token.removeprefix("ugtoken.").split(".")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
        return None
    body, signature = parts
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    if not hmac.compare_digest(expected, signature):
        return None
    return json.loads(_b64url_decode(body))


def _flip_char(value: str, index: int) -> str:
    replacement = "A" if value[index] != "A" else "B"
    return value[:index] + replacement + value[index + 1 :]


# ---------------------------------------------------------------------------
# mint_mcp_service_token: round-trip, claims, tamper, bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["github-review-broker", "cursor-cloud"])
def test_mint_roundtrip_independent_verifier(service: str) -> None:
    token = mint.mint_service_access_token(
        secret=SECRET, issuer=ISSUER, resource=RESOURCE, service=service
    )
    claims = _independent_verify(token, SECRET)
    assert claims is not None
    body, signature = token.removeprefix("ugtoken.").split(".")
    reserialized = (
        base64.urlsafe_b64encode(
            json.dumps(claims, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    assert reserialized == body
    assert len(signature) == 43  # 32-byte HMAC -> 43 unpadded base64url chars
    assert _independent_verify(token, WRONG_SECRET) is None


@pytest.mark.parametrize(
    ("service", "expected_scope"),
    [
        ("github-review-broker", ["unigrok:connect", "unigrok:review"]),
        ("cursor-cloud", ["unigrok:connect", "unigrok:invoke", "unigrok:status"]),
    ],
)
@pytest.mark.parametrize("ttl", [1, 137, 600])
def test_mint_claims_exact(service: str, expected_scope: list[str], ttl: int) -> None:
    token = mint.mint_service_access_token(
        secret=SECRET,
        issuer=ISSUER,
        resource=RESOURCE,
        service=service,
        ttl_seconds=ttl,
        now=FIXED_NOW,
        jti=FIXED_JTI,
    )
    claims = _independent_verify(token, SECRET)
    assert claims == {
        "aud": RESOURCE,
        "exp": FIXED_NOW + ttl,
        "iat": FIXED_NOW,
        "iss": ISSUER,
        "jti": FIXED_JTI,
        "kind": "service",
        "scope": expected_scope,
        "sub": f"service:{service}",
        "v": 1,
    }


def test_mint_jti_unique_and_shaped() -> None:
    jtis = [
        mint._service_access_claims(issuer=ISSUER, resource=RESOURCE)["jti"] for _ in range(50)
    ]
    assert len(set(jtis)) == 50
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{32}", jti) for jti in jtis)


@pytest.mark.parametrize("tamper", ["flip_body", "flip_sig", "strip_prefix", "wrong_secret"])
def test_mint_tamper_rejected(tamper: str) -> None:
    token = mint.mint_service_access_token(secret=SECRET, issuer=ISSUER, resource=RESOURCE)
    body, signature = token.removeprefix("ugtoken.").split(".")
    if tamper == "flip_body":
        forged = f"ugtoken.{_flip_char(body, len(body) // 2)}.{signature}"
    elif tamper == "flip_sig":
        forged = f"ugtoken.{body}.{_flip_char(signature, len(signature) // 2)}"
    elif tamper == "strip_prefix":
        forged = f"{body}.{signature}"
    else:
        resigned = (
            base64.urlsafe_b64encode(
                hmac.new(WRONG_SECRET.encode(), body.encode(), hashlib.sha256).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        forged = f"ugtoken.{body}.{resigned}"
    assert _independent_verify(forged, SECRET) is None


@pytest.mark.parametrize("ttl", [0, 601, -5])
def test_mint_ttl_out_of_bounds_rejected(ttl: int) -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be 1..600"):
        mint.mint_service_access_token(
            secret=SECRET, issuer=ISSUER, resource=RESOURCE, ttl_seconds=ttl
        )


@pytest.mark.parametrize("ttl", [1, 600])
def test_mint_ttl_boundary_accepted(ttl: int) -> None:
    token = mint.mint_service_access_token(
        secret=SECRET, issuer=ISSUER, resource=RESOURCE, ttl_seconds=ttl, now=FIXED_NOW
    )
    claims = _independent_verify(token, SECRET)
    assert claims is not None
    assert claims["exp"] - claims["iat"] == ttl


@pytest.mark.parametrize(
    ("length", "valid"), [(31, False), (32, True), (4096, True), (4097, False)]
)
def test_mint_secret_length_bounds(length: int, valid: bool) -> None:
    secret = "s" * length
    if valid:
        token = mint.mint_service_access_token(secret=secret, issuer=ISSUER, resource=RESOURCE)
        assert _independent_verify(token, secret) is not None
    else:
        with pytest.raises(ValueError, match="secret length is invalid"):
            mint.mint_service_access_token(secret=secret, issuer=ISSUER, resource=RESOURCE)


def test_mint_unknown_service_rejected() -> None:
    with pytest.raises(ValueError, match="service not allowed"):
        mint.mint_service_access_token(
            secret=SECRET, issuer=ISSUER, resource=RESOURCE, service="evil-svc"
        )


@pytest.mark.parametrize(
    ("service", "scope"),
    [
        ("github-review-broker", "unigrok:invoke"),
        ("cursor-cloud", "unigrok:review"),
        ("github-review-broker", "unigrok:connect"),
    ],
)
def test_mint_disallowed_scope_rejected(service: str, scope: str) -> None:
    with pytest.raises(ValueError, match="scope not allowed"):
        mint.mint_service_access_token(
            secret=SECRET, issuer=ISSUER, resource=RESOURCE, service=service, scope=scope
        )


@pytest.mark.parametrize(
    ("issuer", "resource", "match"),
    [
        ("http://control.grokmcp.org", RESOURCE, "issuer must be an https origin"),
        ("https://control.grokmcp.org/", RESOURCE, "issuer must be an https origin"),
        (ISSUER, "https://mcp.grokmcp.org/", "resource must be https://"),
        (ISSUER, "https://mcp.grokmcp.org/mcp/", "resource must be https://"),
        (ISSUER, "http://mcp.grokmcp.org/mcp", "resource must be https://"),
    ],
)
def test_mint_issuer_and_resource_validation(issuer: str, resource: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        mint.mint_service_access_token(secret=SECRET, issuer=issuer, resource=resource)


# ---------------------------------------------------------------------------
# mint_mcp_service_token: CLI subprocess surface
# ---------------------------------------------------------------------------


def _run_mint_cli(
    args: list[str] | None = None, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "UNIGROK_MCP_TOKEN_SECRET": SECRET,
        "UNIGROK_OAUTH_ISSUER": ISSUER,
        "UNIGROK_MCP_RESOURCE_URL": RESOURCE,
    }
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "mint_mcp_service_token.py"), *(args or [])],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cli_default_prints_token_to_stdout_only() -> None:
    result = _run_mint_cli()
    assert result.returncode == 0
    assert TOKEN_RE.fullmatch(result.stdout)  # token only, no trailing newline when piped
    assert result.stderr == ""
    assert SECRET not in result.stdout + result.stderr
    claims = _independent_verify(result.stdout, SECRET)
    assert claims is not None
    assert claims["sub"] == "service:github-review-broker"
    assert claims["exp"] - claims["iat"] == 600


def test_cli_print_claims_writes_metadata_only_to_stderr() -> None:
    result = _run_mint_cli(
        args=["--print-claims"],
        extra_env={
            "UNIGROK_SERVICE_NAME": "cursor-cloud",
            "UNIGROK_SERVICE_SCOPE": "unigrok:invoke",
            "UNIGROK_TOKEN_TTL_SECONDS": "300",
        },
    )
    assert result.returncode == 0
    assert TOKEN_RE.fullmatch(result.stdout)
    metadata = json.loads(result.stderr)
    assert set(metadata) == {"exp", "scope", "sub"}
    assert metadata["sub"] == "service:cursor-cloud"
    assert metadata["scope"] == ["unigrok:connect", "unigrok:invoke", "unigrok:status"]
    assert SECRET not in result.stdout + result.stderr
    claims = _independent_verify(result.stdout, SECRET)
    assert claims is not None
    assert claims["exp"] - claims["iat"] == 300


@pytest.mark.parametrize(
    ("ttl", "message"),
    [("601", "ttl_seconds must be 1..600"), ("abc", "must be an integer")],
)
def test_cli_bad_ttl_env_fails_without_leaking(ttl: str, message: str) -> None:
    result = _run_mint_cli(extra_env={"UNIGROK_TOKEN_TTL_SECONDS": ttl})
    assert result.returncode != 0
    assert result.stdout == ""
    assert message in result.stderr
    assert SECRET not in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# github-grok-review: evidence provenance and SHA validation
# ---------------------------------------------------------------------------


def _provenance_kwargs() -> dict[str, Any]:
    return {
        "repository": "owner/repo",
        "number": 42,
        "pr": {"head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}, "title": "Title"},
        "diff": "diff --git a/f b/f",
        "discussion": "- user: COMMENTED — ok",
    }


def test_evidence_provenance_deterministic() -> None:
    first = review._evidence_provenance(**_provenance_kwargs())
    second = review._evidence_provenance(**_provenance_kwargs())
    assert first == second
    assert set(first) == {"base_sha", "evidence_sha256", "head_sha"}
    assert re.fullmatch(r"[0-9a-f]{64}", first["evidence_sha256"])
    assert first["head_sha"] == "a" * 40
    assert first["base_sha"] == "b" * 40


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "owner/other"),
        ("number", 43),
        ("diff", "diff --git a/f b/f "),
        ("discussion", "- user: COMMENTED — ok!"),
    ],
)
def test_evidence_provenance_field_change_flips_digest(field: str, value: Any) -> None:
    baseline = review._evidence_provenance(**_provenance_kwargs())
    kwargs = _provenance_kwargs()
    kwargs[field] = value
    assert review._evidence_provenance(**kwargs)["evidence_sha256"] != baseline["evidence_sha256"]


@pytest.mark.parametrize(
    "mutate_pr",
    [
        lambda pr: pr.update(title="Title!"),
        lambda pr: pr["head"].update(sha="c" * 40),
        lambda pr: pr["base"].update(sha="d" * 40),
    ],
)
def test_evidence_provenance_pr_change_flips_digest(mutate_pr: Any) -> None:
    baseline = review._evidence_provenance(**_provenance_kwargs())
    kwargs = _provenance_kwargs()
    mutate_pr(kwargs["pr"])
    assert review._evidence_provenance(**kwargs)["evidence_sha256"] != baseline["evidence_sha256"]


def test_evidence_provenance_rejects_invalid_head_sha() -> None:
    kwargs = _provenance_kwargs()
    kwargs["pr"]["head"]["sha"] = "not-a-sha"
    with pytest.raises(RuntimeError, match="invalid head commit SHA"):
        review._evidence_provenance(**kwargs)


def test_commit_sha_accepts_valid_and_lowercases_uppercase() -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert review._commit_sha(sha, label="head") == sha
    assert review._commit_sha(sha.upper(), label="head") == sha
    assert review._commit_sha(f"  {sha}  ", label="head") == sha


@pytest.mark.parametrize(
    "value",
    ["a" * 39, "a" * 41, "g" * 40, None, "", 1234567890, "deadbeef"],
)
def test_commit_sha_rejects_invalid(value: Any) -> None:
    with pytest.raises(RuntimeError, match="invalid expected head commit SHA"):
        review._commit_sha(value, label="expected head")


# ---------------------------------------------------------------------------
# github-grok-review: event parsing and run URL
# ---------------------------------------------------------------------------


def test_pull_number_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INPUT_PR_NUMBER", "45")
    assert review._pull_number({"pull_request": {"number": 7}}) == 45


def test_pull_number_from_pull_request_event() -> None:
    assert review._pull_number({"pull_request": {"number": 7}}) == 7


def test_pull_number_from_issue_comment_event() -> None:
    event = {"issue": {"number": 9, "pull_request": {"url": "https://example.invalid"}}}
    assert review._pull_number(event) == 9


@pytest.mark.parametrize("event", [{}, {"issue": {"number": 5}}])
def test_pull_number_rejects_non_pr_events(event: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError, match="does not identify a pull request"):
        review._pull_number(event)


def test_workflow_run_url_for_digit_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    assert review._workflow_run_url("o/r") == "https://github.com/o/r/actions/runs/123456"
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com/")
    assert review._workflow_run_url("o/r") == "https://github.com/o/r/actions/runs/123456"


@pytest.mark.parametrize("run_id", ["", "abc", "12 34", "-5"])
def test_workflow_run_url_none_for_non_digit_run_id(
    monkeypatch: pytest.MonkeyPatch, run_id: str
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    assert review._workflow_run_url("o/r") is None


def test_workflow_run_url_none_for_foreign_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://ghe.example.com")
    assert review._workflow_run_url("o/r") is None


# ---------------------------------------------------------------------------
# github-grok-review: comment rendering and upsert
# ---------------------------------------------------------------------------

PROVENANCE = {"head_sha": "a" * 40, "base_sha": "b" * 40, "evidence_sha256": "c" * 64}


def test_format_comment_renders_marker_metadata_and_run_url() -> None:
    data = {
        "review": "Looks good.",
        "model": "grok-4.5",
        "plane": "cli",
        "route": "direct",
        "cost_usd": 0.000049999,
    }
    run_url = "https://github.com/o/r/actions/runs/1"
    body = review._format_comment(data, provenance=PROVENANCE, run_url=run_url)
    assert body.startswith(review.MARKER + "\n")
    assert "Looks good." in body
    assert "Model: `grok-4.5` · Plane: `cli` · Route: `direct` · Cost: `$0.00005`" in body
    assert f"Reviewed head: `{'a' * 40}` · Base: `{'b' * 40}`" in body
    assert f"Evidence: `sha256:{'c' * 64}`" in body
    assert f"[workflow run]({run_url})" in body


def test_format_comment_defaults_without_run_url() -> None:
    body = review._format_comment({}, provenance=PROVENANCE, run_url=None)
    assert "No review returned." in body
    assert "Model: `unknown` · Plane: `unknown` · Route: `unknown`" in body
    assert "workflow run" not in body


@pytest.mark.parametrize(
    ("cost", "rendered"),
    [
        (None, "$0.00000"),
        (0, "$0.00000"),
        ("0.12345", "$0.12345"),
        (5, "$5.00000"),
    ],
)
def test_format_comment_cost_variants(cost: Any, rendered: str) -> None:
    body = review._format_comment({"cost_usd": cost}, provenance=PROVENANCE)
    assert f"Cost: `{rendered}`" in body


class _GithubRecorder:
    def __init__(self, comments: list[dict[str, Any]]) -> None:
        self.comments = comments
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(
        self,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, url, payload))
        return self.comments if method == "GET" else {}


def _run_upsert(
    monkeypatch: pytest.MonkeyPatch, comments: list[dict[str, Any]]
) -> _GithubRecorder:
    recorder = _GithubRecorder(comments)
    monkeypatch.setattr(review, "_github_request", recorder)
    review._upsert_comment("https://api.example", "owner/repo", 7, "gh-token", "NEW-BODY")
    return recorder


def test_upsert_comment_patches_existing_marker_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {
        "url": "https://api.example/repos/owner/repo/issues/comments/11",
        "body": f"{review.MARKER}\nold body",
        "user": {"login": "github-actions[bot]"},
    }
    recorder = _run_upsert(monkeypatch, [existing])
    assert [call[0] for call in recorder.calls] == ["GET", "PATCH"]
    assert recorder.calls[1] == ("PATCH", existing["url"], {"body": "NEW-BODY"})


@pytest.mark.parametrize(
    "comments",
    [
        [],
        [{"url": "u", "body": "<!-- unigrok-review --> hi", "user": {"login": "someone"}}],
        [{"url": "u", "body": "plain comment", "user": {"login": "github-actions[bot]"}}],
    ],
)
def test_upsert_comment_posts_when_no_bot_marker_comment(
    monkeypatch: pytest.MonkeyPatch, comments: list[dict[str, Any]]
) -> None:
    recorder = _run_upsert(monkeypatch, comments)
    assert [call[0] for call in recorder.calls] == ["GET", "POST"]
    assert recorder.calls[1] == (
        "POST",
        "https://api.example/repos/owner/repo/issues/7/comments",
        {"body": "NEW-BODY"},
    )


# ---------------------------------------------------------------------------
# github-grok-review: gateway auth resolution
# ---------------------------------------------------------------------------


def test_gateway_bearer_static_fallback_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert review._gateway_bearer_token() == ""
    monkeypatch.setenv("UNIGROK_CLIENT_TOKEN", "  static-client-token  ")
    assert review._gateway_bearer_token() == "static-client-token"


def test_gateway_bearer_mints_broker_token_from_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_MCP_TOKEN_SECRET", SECRET)
    claims = _independent_verify(review._gateway_bearer_token(), SECRET)
    assert claims is not None
    assert claims["aud"] == "https://mcp.grokmcp.org/mcp"
    assert claims["iss"] == "https://control.grokmcp.org"
    assert claims["sub"] == "service:github-review-broker"
    assert claims["kind"] == "service"
    assert claims["v"] == 1
    assert claims["scope"] == ["unigrok:connect", "unigrok:review"]
    assert claims["exp"] - claims["iat"] == 600


@pytest.mark.parametrize(
    ("configured", "expected_aud"),
    [
        ("https://mcp.grokmcp.org", "https://mcp.grokmcp.org/mcp"),
        ("https://mcp.grokmcp.org/mcp/", "https://mcp.grokmcp.org/mcp"),
        ("https://foo.com/", "https://foo.com/mcp"),
    ],
)
def test_gateway_bearer_normalizes_resource_to_mcp_audience(
    monkeypatch: pytest.MonkeyPatch, configured: str, expected_aud: str
) -> None:
    monkeypatch.setenv("UNIGROK_MCP_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("UNIGROK_MCP_RESOURCE_URL", configured)
    claims = _independent_verify(review._gateway_bearer_token(), SECRET)
    assert claims is not None
    assert claims["aud"] == expected_aud


# ---------------------------------------------------------------------------
# github-grok-review: error rendering
# ---------------------------------------------------------------------------


def test_format_exc_renders_nested_exception_group() -> None:
    leaf = ValueError("leaf failure")
    leaf.__cause__ = KeyError("root cause")
    group = ExceptionGroup("outer", [leaf, ExceptionGroup("inner", [TypeError("typed")])])
    text = review._format_exc(group)
    assert text.startswith("ExceptionGroup: outer")
    assert "[1] ValueError: leaf failure" in text
    assert "caused by: KeyError: 'root cause'" in text
    assert "[2] ExceptionGroup: inner" in text
    assert "[1] TypeError: typed" in text


def test_format_exc_shows_context_unless_suppressed() -> None:
    chained = RuntimeError("wrapper")
    chained.__context__ = OSError("root")
    assert review._format_exc(chained) == "RuntimeError: wrapper\n  caused by: OSError: root"
    chained.__suppress_context__ = True
    assert review._format_exc(chained) == "RuntimeError: wrapper"
