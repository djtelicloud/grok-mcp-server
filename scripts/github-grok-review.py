#!/usr/bin/env python3
"""Fetch a PR as untrusted evidence, ask UniGrok over MCP, update one comment."""

from __future__ import annotations

import asyncio
import json
import os
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


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _github_request(url: str, token: str, *, accept: str = "application/vnd.github+json", method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
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
        with urllib.request.urlopen(request, timeout=30) as response:
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


async def _call_unigrok(arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.environ.get("UNIGROK_MCP_URL", "http://127.0.0.1:4765/mcp")
    headers = {"X-Client-ID": "github-actions"}
    gateway_token = os.environ.get("UNIGROK_CLIENT_TOKEN", "").strip()
    if gateway_token:
        headers["Authorization"] = f"Bearer {gateway_token}"
    async with httpx.AsyncClient(headers=headers, timeout=180.0) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=180)) as session:
                await session.initialize()
                result = await session.call_tool("review_pull_request", arguments)
    if result.isError:
        text = "\n".join(getattr(item, "text", "") for item in result.content)
        raise RuntimeError(text or "UniGrok review tool failed")
    structured = result.structuredContent
    if not isinstance(structured, dict):
        raise RuntimeError("UniGrok returned no structured review payload")
    return structured


def _format_comment(data: dict[str, Any]) -> str:
    review = str(data.get("review") or "No review returned.")
    return (
        f"{MARKER}\n## @grok review for Codex\n\n{review}\n\n"
        "---\n"
        f"Model: `{data.get('model', 'unknown')}` · Plane: `{data.get('plane', 'unknown')}` · "
        f"Route: `{data.get('route', 'unknown')}` · Cost: `${float(data.get('cost_usd') or 0):.5f}`\n\n"
        "This is advisory evidence. Codex remains the sole landing and merge authority."
    )


def _upsert_comment(api: str, repository: str, number: int, token: str, body: str) -> None:
    comments_url = f"{api}/repos/{repository}/issues/{number}/comments?per_page=100"
    comments = _github_request(comments_url, token)
    existing = next(
        (item for item in comments if MARKER in str(item.get("body", "")) and item.get("user", {}).get("login") == "github-actions[bot]"),
        None,
    )
    if existing:
        _github_request(existing["url"], token, method="PATCH", payload={"body": body})
    else:
        _github_request(comments_url.split("?", 1)[0], token, method="POST", payload={"body": body})


async def main() -> None:
    token = _required("GITHUB_TOKEN")
    repository = _required("GITHUB_REPOSITORY")
    event = json.loads(Path(_required("GITHUB_EVENT_PATH")).read_text(encoding="utf-8"))
    number = _pull_number(event)
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    pr_url = f"{api}/repos/{repository}/pulls/{number}"
    pr = _github_request(pr_url, token)
    diff = _github_request(pr_url, token, accept="application/vnd.github.v3.diff")
    reviews = _github_request(f"{pr_url}/reviews?per_page=100", token)
    discussion = "\n".join(
        f"- {item.get('user', {}).get('login', 'unknown')}: {item.get('state', 'COMMENTED')} — {item.get('body') or '(no body)'}"
        for item in reviews
    )[:MAX_DISCUSSION_CHARS]
    truncated = len(diff) > MAX_DIFF_CHARS
    diff = diff[:MAX_DIFF_CHARS]
    if truncated:
        diff += "\n\n[Diff truncated by UniGrok GitHub review safety limit.]"
    data = await _call_unigrok(
        {
            "repository": repository,
            "pull_number": number,
            "title": pr.get("title") or "",
            "diff": diff,
            "ci_summary": "GitHub Actions evidence is reviewed separately by Codex's maintainer sweep.",
            "review_comments": discussion,
            "plane": os.environ.get("UNIGROK_REVIEW_PLANE", "cli"),
        }
    )
    _upsert_comment(api, repository, number, token, _format_comment(data))
    print(f"Updated UniGrok review for {repository}#{number}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
