#!/usr/bin/env python3
"""Fetch a PR as untrusted evidence, ask UniGrok over MCP, update one comment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MARKER = "<!-- unigrok-review -->"
MAX_DIFF_CHARS = 90_000
MAX_DISCUSSION_CHARS = 8_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
REVIEW_POLL_WAIT_SECONDS = 20
# Keep the complete review inside the broker token's 600-second maximum TTL,
# with headroom for connection setup, initialization, and the initial sync window.
REVIEW_POLL_TIMEOUT_SECONDS = 500


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _github_request(
    url: str,
    token: str,
    *,
    accept: str = "application/vnd.github+json",
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    # URLs are GitHub REST API https endpoints built from the runner's
    # GITHUB_API_URL / GitHub-returned resource links — no untrusted schemes.
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        method=method,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "unigrok-review",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1_000]
        raise RuntimeError(f"GitHub API {exc.code} for {url}: {detail}") from exc
    if accept.endswith(".diff"):
        return raw.decode("utf-8", errors="replace")
    return json.loads(raw or b"{}")


def _pull_number(event: dict[str, Any]) -> int:
    manual = os.environ.get("INPUT_PR_NUMBER", "").strip()
    if manual:
        return int(manual)
    pull = event.get("pull_request") or event.get("issue", {}).get("pull_request")
    if event.get("pull_request"):
        return int(event["pull_request"]["number"])
    if pull:
        return int(event["issue"]["number"])
    raise RuntimeError("event does not identify a pull request")


def _commit_sha(value: Any, *, label: str) -> str:
    sha = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise RuntimeError(f"GitHub returned an invalid {label} commit SHA")
    return sha


def _expected_head_sha(event: dict[str, Any]) -> str | None:
    configured = os.environ.get("EXPECTED_HEAD_SHA", "").strip()
    event_sha = (event.get("pull_request") or {}).get("head", {}).get("sha")
    value = configured or event_sha
    return _commit_sha(value, label="expected head") if value else None


def _expected_base_sha(event: dict[str, Any]) -> str | None:
    configured = os.environ.get("EXPECTED_BASE_SHA", "").strip()
    event_sha = (event.get("pull_request") or {}).get("base", {}).get("sha")
    value = configured or event_sha
    return _commit_sha(value, label="expected base") if value else None


def _evidence_provenance(
    *,
    repository: str,
    number: int,
    pr: dict[str, Any],
    diff: str,
    discussion: str,
) -> dict[str, str]:
    head_sha = _commit_sha(pr.get("head", {}).get("sha"), label="head")
    base_sha = _commit_sha(pr.get("base", {}).get("sha"), label="base")
    envelope = {
        "base_sha": base_sha,
        "diff": diff,
        "discussion": discussion,
        "head_sha": head_sha,
        "pull_number": number,
        "repository": repository,
        "title": str(pr.get("title") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(
            envelope,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "base_sha": base_sha,
        "evidence_sha256": digest,
        "head_sha": head_sha,
    }


def _workflow_run_url(repository: str) -> str | None:
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if not run_id.isdigit():
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    if server not in {"https://github.com", "https://github.com/"}:
        return None
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def _gateway_bearer_token() -> str:
    """Resolve MCP auth: mint when possible, else use the static fallback.

    Production twin validates via Control introspection. Long-lived static
    client keys must not be installed on Cloud Run. When
    ``UNIGROK_MCP_TOKEN_SECRET`` is set (same as Control ``MCP_TOKEN_SECRET``),
    mint ``service:github-review-broker`` with ``unigrok:review``.
    """
    secret = os.environ.get("UNIGROK_MCP_TOKEN_SECRET", "").strip()
    if not secret:
        return os.environ.get("UNIGROK_CLIENT_TOKEN", "").strip()
    # Import path works when run as ``uv run python scripts/github-grok-review.py``
    # with repo root on sys.path (uv / Actions checkout default).
    try:
        from scripts.mint_mcp_service_token import mint_service_access_token
    except ImportError:  # pragma: no cover - script dir execution
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from scripts.mint_mcp_service_token import mint_service_access_token
    issuer = os.environ.get("UNIGROK_OAUTH_ISSUER", "https://control.grokmcp.org").strip()
    resource = os.environ.get(
        "UNIGROK_MCP_RESOURCE_URL",
        os.environ.get("UNIGROK_MCP_URL", "https://mcp.grokmcp.org/mcp").strip(),
    ).strip()
    if resource.endswith("/"):
        resource = resource.rstrip("/")
    # Resource must be the OAuth audience (.../mcp), not a path typo.
    if not resource.endswith("/mcp"):
        if resource.endswith(".org") or resource.endswith(".com"):
            resource = f"{resource}/mcp"
    raw_ttl = os.environ.get("UNIGROK_TOKEN_TTL_SECONDS", "").strip()
    try:
        ttl_seconds = int(raw_ttl) if raw_ttl else None
    except ValueError as exc:
        raise ValueError("UNIGROK_TOKEN_TTL_SECONDS must be an integer") from exc
    return mint_service_access_token(
        secret=secret,
        issuer=issuer,
        resource=resource,
        service=os.environ.get("UNIGROK_SERVICE_NAME", "github-review-broker").strip(),
        scope=os.environ.get("UNIGROK_SERVICE_SCOPE", "unigrok:review").strip(),
        ttl_seconds=ttl_seconds,
    )


def _tool_payload(result: Any, *, tool_name: str) -> dict[str, Any]:
    if result.isError:
        detail = "\n".join(getattr(item, "text", "") for item in result.content)
        raise RuntimeError((detail or f"UniGrok {tool_name} tool failed")[:2_000])
    structured = result.structuredContent
    if not isinstance(structured, dict):
        raise RuntimeError(f"UniGrok {tool_name} returned no structured payload")
    return structured


def _completed_review(payload: dict[str, Any]) -> dict[str, Any]:
    review_text = str(payload.get("review") or payload.get("text") or "").strip()
    if not review_text:
        raise RuntimeError("UniGrok completed the review job without review text")
    if payload.get("review") == review_text:
        return payload
    completed = dict(payload)
    completed["review"] = review_text
    return completed


async def _await_review_result(
    session: ClientSession,
    initial: Any,
    *,
    timeout_seconds: float = REVIEW_POLL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve an immediate review or poll its one durable job to a terminal result."""
    payload = _tool_payload(initial, tool_name="review_pull_request")
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout_seconds))
    job_id: str | None = None

    while True:
        status = str(payload.get("status") or "").strip().lower()
        raw_candidate = str(payload.get("job_id") or "").strip().lower()
        if raw_candidate:
            if not JOB_ID_RE.fullmatch(raw_candidate):
                raise RuntimeError("UniGrok review returned an invalid durable job id")
            if job_id is None:
                job_id = raw_candidate
            elif raw_candidate != job_id:
                raise RuntimeError("UniGrok review polling switched durable job ids")
        if not status:
            # Compatibility with the original hosted review tool, which
            # returned its final schema directly without a status envelope.
            return _completed_review(payload)
        if status == "complete":
            return _completed_review(payload)
        if status in {"error", "lost", "cancelled", "canceled"}:
            detail = str(payload.get("text") or payload.get("stop_reason") or status)
            raise RuntimeError(f"UniGrok review job {status}: {detail[:2_000]}")
        if status == "continue" and payload.get("poll") is not True:
            raise RuntimeError(
                "UniGrok review needs an explicit continuation that the review-only "
                "broker is not authorized to start"
            )
        if status not in {"pending", "continue"}:
            raise RuntimeError(f"UniGrok review returned unknown status: {status[:80]}")

        if job_id is None:
            raise RuntimeError("UniGrok review returned an invalid durable job id")

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(
                f"UniGrok review job did not finish within {int(timeout_seconds)} seconds"
            )
        wait_seconds = max(1, min(REVIEW_POLL_WAIT_SECONDS, int(remaining)))
        polled = await session.call_tool(
            "agent_result",
            {"job_id": job_id, "wait_seconds": wait_seconds},
        )
        payload = _tool_payload(polled, tool_name="agent_result")


async def _call_unigrok(arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.environ.get("UNIGROK_MCP_URL", "http://127.0.0.1:4765/mcp")
    headers = {"X-Client-ID": "github-actions", "X-Caller": "github-review-broker"}
    gateway_token = _gateway_bearer_token()
    if gateway_token:
        headers["Authorization"] = f"Bearer {gateway_token}"
    timeout = httpx.Timeout(connect=15.0, read=540.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=540)
            ) as session:
                await session.initialize()
                initial = await session.call_tool("review_pull_request", arguments)
                return await _await_review_result(session, initial)


def _format_comment(
    data: dict[str, Any],
    *,
    provenance: dict[str, str],
    run_url: str | None = None,
) -> str:
    review = str(data.get("review") or "No review returned.")
    run = f" · [workflow run]({run_url})" if run_url else ""
    return (
        f"{MARKER}\n## @grok review for Codex\n\n{review}\n\n"
        "---\n"
        f"Model: `{data.get('model', 'unknown')}` · Plane: `{data.get('plane', 'unknown')}` · "
        f"Route: `{data.get('route', 'unknown')}` · "
        f"Cost: `${float(data.get('cost_usd') or 0):.5f}`\n\n"
        f"Reviewed head: `{provenance['head_sha']}` · Base: `{provenance['base_sha']}`\n\n"
        f"Evidence: `sha256:{provenance['evidence_sha256']}`{run}\n\n"
        "This is advisory evidence. Codex remains the sole landing and merge authority."
    )


def _upsert_comment(api: str, repository: str, number: int, token: str, body: str) -> None:
    comments_url = f"{api}/repos/{repository}/issues/{number}/comments?per_page=100"
    comments = _github_request(comments_url, token)
    existing = next(
        (
            item
            for item in comments
            if MARKER in str(item.get("body", ""))
            # Ghost/deleted accounts serialize as "user": null — guard with `or {}`
            # so one ghost comment cannot crash the whole upsert scan.
            and (item.get("user") or {}).get("login") == "github-actions[bot]"
        ),
        None,
    )
    if existing:
        _github_request(existing["url"], token, method="PATCH", payload={"body": body})
    else:
        _github_request(comments_url.split("?", 1)[0], token, method="POST", payload={"body": body})


def _load_event() -> dict[str, Any]:
    return json.loads(Path(_required("GITHUB_EVENT_PATH")).read_text(encoding="utf-8"))


def _build_discussion(reviews: list[dict[str, Any]]) -> str:
    return "\n".join(
        # Ghost/deleted reviewers serialize as "user": null — `or {}` keeps one
        # ghost review from crashing the whole discussion build.
        f"- {(item.get('user') or {}).get('login', 'unknown')}: "
        f"{item.get('state', 'COMMENTED')} — {item.get('body') or '(no body)'}"
        for item in reviews
    )[:MAX_DISCUSSION_CHARS]


async def main() -> None:
    token = _required("GITHUB_TOKEN")
    repository = _required("GITHUB_REPOSITORY")
    event = _load_event()
    number = _pull_number(event)
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    pr_url = f"{api}/repos/{repository}/pulls/{number}"
    pr = _github_request(pr_url, token)
    expected_head = _expected_head_sha(event)
    expected_base = _expected_base_sha(event)
    fetched_head = _commit_sha(pr.get("head", {}).get("sha"), label="head")
    fetched_base = _commit_sha(pr.get("base", {}).get("sha"), label="base")
    if expected_head and fetched_head != expected_head:
        raise RuntimeError(
            "refusing stale review: event head "
            f"{expected_head} no longer matches PR head {fetched_head}"
        )
    if expected_base and fetched_base != expected_base:
        raise RuntimeError(
            "refusing stale review: event base "
            f"{expected_base} no longer matches PR base {fetched_base}"
        )
    # The PR diff endpoint is mutable. Fetching it after recording the PR SHAs
    # permits an A -> B -> A race to bind B's diff to A's provenance. Comparing
    # two validated full commit ids makes the reviewed evidence immutable.
    compare_url = (
        f"{api}/repos/{repository}/compare/{fetched_base}...{fetched_head}"
    )
    diff = _github_request(
        compare_url,
        token,
        accept="application/vnd.github.v3.diff",
    )
    reviews = _github_request(f"{pr_url}/reviews?per_page=100", token)
    discussion = _build_discussion(reviews)
    truncated = len(diff) > MAX_DIFF_CHARS
    diff = diff[:MAX_DIFF_CHARS]
    if truncated:
        diff += "\n\n[Diff truncated by UniGrok GitHub review safety limit.]"
    provenance = _evidence_provenance(
        repository=repository,
        number=number,
        pr=pr,
        diff=diff,
        discussion=discussion,
    )
    data = await _call_unigrok(
        {
            "repository": repository,
            "pull_number": number,
            "title": pr.get("title") or "",
            "diff": diff,
            "ci_summary": (
                "GitHub Actions evidence is reviewed separately by Codex's maintainer sweep.\n"
                "Trusted fetcher provenance:\n"
                f"- head: {provenance['head_sha']}\n"
                f"- base: {provenance['base_sha']}\n"
                f"- bounded evidence sha256: {provenance['evidence_sha256']}"
            ),
            "review_comments": discussion,
        }
    )
    current = _github_request(pr_url, token)
    current_head = _commit_sha(current.get("head", {}).get("sha"), label="current head")
    current_base = _commit_sha(current.get("base", {}).get("sha"), label="current base")
    if (
        current_head != provenance["head_sha"]
        or current_base != provenance["base_sha"]
    ):
        raise RuntimeError(
            "refusing stale review comment: PR base/head changed while Grok was "
            "reviewing "
            f"(base {provenance['base_sha']} -> {current_base}; "
            f"head {provenance['head_sha']} -> {current_head})"
        )
    _upsert_comment(
        api,
        repository,
        number,
        token,
        _format_comment(
            data,
            provenance=provenance,
            run_url=_workflow_run_url(repository),
        ),
    )
    print(
        f"Updated UniGrok review for {repository}#{number} "
        f"at {provenance['head_sha']} ({provenance['evidence_sha256']})"
    )


def _format_exc(exc: BaseException) -> str:
    """Surface nested ExceptionGroup / TaskGroup causes for Actions logs."""
    parts = [f"{type(exc).__name__}: {exc}"]
    if isinstance(exc, BaseExceptionGroup):
        for i, sub in enumerate(exc.exceptions, 1):
            parts.append(f"  [{i}] {_format_exc(sub)}")
    cause = exc.__cause__
    if cause is None and not exc.__suppress_context__:
        cause = exc.__context__
    if cause is not None and cause is not exc:
        parts.append(f"  caused by: {_format_exc(cause)}")
    return "\n".join(parts)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"error: {_format_exc(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc
