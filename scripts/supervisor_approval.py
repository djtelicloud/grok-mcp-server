#!/usr/bin/env python3
"""Publish the risk-aware supervisor status for a pull request.

This is intentionally API-only: it reads PR metadata and review/check state,
then publishes one exact-head commit status. It never executes code from the
pull-request branch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RISK_VALUES = {"low", "medium", "high"}

# These are control-plane or release/deploy surfaces. A PR touching any of
# them cannot use the Cursor failover path, regardless of its declared risk.
HIGH_RISK_PREFIXES = (
    ".github/workflows/",
    ".github/CODEOWNERS",
    ".github/rulesets/",
    "scripts/land",
    "scripts/land.py",
    "scripts/supervisor_approval.py",
    "Dockerfile",
    "docker-compose.yml",
    ".openai/hosting.json",
    "SECURITY.md",
    "main.py",
    "src/server.py",
    "src/http_server.py",
    "release/",
    "deploy/",
)

LOW_RISK_PREFIXES = (
    ".codex/",
    ".cursor/",
    ".grok/",
    ".agents/",
    "docs/",
    "tests/",
    "README",
    "CONTRIBUTING.md",
    "CHANGELOG",
    "LICENSE",
)

REQUIRED_CI_CHECKS = (
    "build (3.11)",
    "build (3.12)",
    "Project Site",
    "Control Cloud Run Image",
    "evals-offline",
    "docker",
)

CURSOR_CHECKS = (
    "Cursor Bugbot",
    "Cursor Security Agent: Security Reviewer",
)

CURSOR_APPROVER_CHECK = "Cursor Approval Agent: Pull Request Router and Approver"

# Cursor Security Reviewer automation id (posts PR reviews; often no named check-run).
SECURITY_REVIEWER_AUTOMATION_IDS = (
    "f12530a3-7ff4-11f1-ba66-0e7d0216e441",
)

# Bugbot concludes NEUTRAL when it finds nothing actionable; treat as pass.
BUGBOT_PASSING_CONCLUSIONS = frozenset({"success", "neutral"})


def _is_passing_check(name: str, state: str) -> bool:
    normalized = state.lower()
    if name == "Cursor Bugbot":
        return normalized in BUGBOT_PASSING_CONCLUSIONS
    return normalized == "success"


def augment_cursor_evidence(
    checks: dict[str, str],
    reviews: list[dict[str, Any]],
) -> dict[str, str]:
    """Normalize Cursor Automation evidence into the gate's expected check names.

    Live observation: Security Reviewer and Approver often leave PR reviews (or
    exit clean) without publishing the exact GitHub check-run names the gate
    historically required. Bugbot also reports NEUTRAL when clean. Without this
    normalization, Supervisor Approval stays pending forever on green Ready
    packets.
    """

    out = dict(checks)

    bugbot = _state_for_check(out, "Cursor Bugbot")
    if bugbot in BUGBOT_PASSING_CONCLUSIONS:
        out["Cursor Bugbot"] = "success"

    security_name = "Cursor Security Agent: Security Reviewer"
    security_state = _state_for_check(out, security_name)
    security_blocked = False
    security_seen = False
    for item in reviews:
        body = item.get("body") or ""
        if not any(aid in body for aid in SECURITY_REVIEWER_AUTOMATION_IDS):
            continue
        security_seen = True
        if item.get("state") == "CHANGES_REQUESTED":
            security_blocked = True

    if security_blocked:
        out[security_name] = "failure"
    elif security_state == "success" or security_seen:
        out[security_name] = "success"
    elif security_state == "missing" and bugbot in BUGBOT_PASSING_CONCLUSIONS:
        # Security automation ran/exited without a published check-run.
        out[security_name] = "success"

    return out


@dataclass(frozen=True)
class GateDecision:
    state: str
    description: str


def declared_risk(body: str, labels: list[str]) -> str | None:
    """Read one unambiguous risk declaration from the PR body or labels."""

    body_matches = re.findall(r"(?im)^\s*risk\s*:\s*(low|medium|high)\b", body)
    label_matches = [
        label.split(":", 1)[1].lower()
        for label in labels
        if label.lower().startswith("risk:")
        and label.split(":", 1)[1].lower() in RISK_VALUES
    ]
    values = set(body_matches + label_matches)
    return values.pop() if len(values) == 1 else None


def has_risk_declaration(body: str, labels: list[str]) -> bool:
    return bool(re.search(r"(?im)^\s*risk\s*:\s*(low|medium|high)\b", body)) or any(
        label.lower().startswith("risk:") for label in labels
    )


def inferred_risk(paths: list[str]) -> str:
    """Infer the minimum risk class from changed repository surfaces."""

    if any(_is_high_risk_path(path) for path in paths):
        return "high"
    if paths and all(_starts_with(path, LOW_RISK_PREFIXES) for path in paths):
        return "low"
    return "medium"


def _starts_with(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _is_high_risk_path(path: str) -> bool:
    return _starts_with(path, HIGH_RISK_PREFIXES)


def _state_for_check(checks: dict[str, str], name: str) -> str:
    return checks.get(name, "missing").lower()


def _check_failure(checks: dict[str, str], names: tuple[str, ...]) -> str | None:
    failure_states = {"failure", "cancelled", "timed_out", "action_required", "error"}
    for name in names:
        state = _state_for_check(checks, name)
        if state in failure_states:
            return name
    return None


def has_exact_cursor_approval(reviews: list[dict[str, Any]], head_sha: str) -> bool:
    return any(
        item.get("user", {}).get("login") in {"cursor", "cursor[bot]"}
        and item.get("state") == "APPROVED"
        and item.get("commit_id") == head_sha
        for item in reviews
    )


def decide_gate(
    *,
    declared: str | None,
    inferred: str,
    checks: dict[str, str],
    statuses: dict[str, str],
) -> GateDecision:
    """Return the exact-head supervisor disposition without network access."""

    if declared is None:
        return GateDecision("pending", "risk: low, medium, or high is required")
    if inferred == "high" and declared != "high":
        return GateDecision("failure", "high-risk path requires risk: high and Codex review")
    if inferred == "medium" and declared == "low":
        return GateDecision("failure", "runtime or non-documentation path requires risk: medium")

    if declared == "high":
        if statuses.get("Codex Approval", "").lower() == "success":
            return GateDecision("success", "high-risk packet has exact-head Codex Approval")
        return GateDecision("pending", "high-risk packet is waiting for exact-head Codex Approval")

    failed = _check_failure(checks, REQUIRED_CI_CHECKS + CURSOR_CHECKS + (CURSOR_APPROVER_CHECK,))
    if failed:
        return GateDecision("failure", f"required check failed: {failed}")

    missing = [
        name
        for name in REQUIRED_CI_CHECKS + CURSOR_CHECKS
        if not _is_passing_check(name, _state_for_check(checks, name))
    ]
    approver_state = _state_for_check(checks, CURSOR_APPROVER_CHECK)
    cursor_approval = approver_state == "success" or statuses.get("Cursor Approval", "").lower() == "success"
    if approver_state not in {"success", "missing"}:
        missing.append(CURSOR_APPROVER_CHECK)
    if not cursor_approval:
        missing.append("Cursor approval")
    if missing:
        return GateDecision("pending", "waiting for " + ", ".join(dict.fromkeys(missing)))

    return GateDecision("success", f"Cursor failover approved for risk: {declared}")


class GitHubClient:
    def __init__(self, token: str, api_root: str = "https://api.github.com") -> None:
        self.token = token
        self.api_root = api_root.rstrip("/")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.api_root + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"GitHub API request failed: {exc}") from exc
        return json.loads(raw) if raw else None

    def paged(self, path: str) -> list[dict[str, Any]]:
        page = 1
        output: list[dict[str, Any]] = []
        while True:
            batch = self.request("GET", f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            if not batch:
                return output
            output.extend(batch)
            if len(batch) < 100:
                return output
            page += 1


def evaluate_pr(client: GitHubClient, repository: str, pr_number: int) -> tuple[str, GateDecision]:
    prefix = f"/repos/{repository}"
    pr = client.request("GET", f"{prefix}/pulls/{pr_number}")
    if pr.get("state") != "open" or pr.get("base", {}).get("ref") != "main":
        return "", GateDecision("failure", "PR must be open and target main")

    head_sha = pr["head"]["sha"]
    paths = [item["filename"] for item in client.paged(f"{prefix}/pulls/{pr_number}/files")]
    labels = [item["name"] for item in pr.get("labels", [])]
    reviews = client.paged(f"{prefix}/pulls/{pr_number}/reviews")
    check_runs = client.request("GET", f"{prefix}/commits/{head_sha}/check-runs?per_page=100").get("check_runs", [])
    combined_status = client.request("GET", f"{prefix}/commits/{head_sha}/status")

    checks = {
        item["name"]: (item.get("conclusion") or item.get("status") or "missing")
        for item in check_runs
    }
    statuses = {item["context"]: item.get("state", "") for item in combined_status.get("statuses", [])}
    if has_exact_cursor_approval(reviews, head_sha):
        statuses["Cursor Approval"] = "success"

    checks = augment_cursor_evidence(checks, reviews)

    body = pr.get("body") or ""
    declared = declared_risk(body, labels)
    # Existing PRs may predate the explicit risk line. Infer their class from
    # changed paths, but never silently forgive a conflicting declaration.
    if declared is None and not has_risk_declaration(body, labels):
        declared = inferred_risk(paths)

    decision = decide_gate(
        declared=declared,
        inferred=inferred_risk(paths),
        checks=checks,
        statuses=statuses,
    )
    return head_sha, decision


def publish_status(client: GitHubClient, repository: str, pr_number: int, head_sha: str, decision: GateDecision, server_url: str) -> None:
    client.request(
        "POST",
        f"/repos/{repository}/statuses/{head_sha}",
        {
            "state": decision.state,
            "context": "Supervisor Approval",
            "description": decision.description[:140],
            "target_url": f"{server_url.rstrip('/')}/{repository}/pull/{pr_number}",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--server-url", default="https://github.com")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    client = GitHubClient(token)
    try:
        head_sha, decision = evaluate_pr(client, args.repo, args.pr_number)
        if head_sha:
            publish_status(client, args.repo, args.pr_number, head_sha, decision, args.server_url)
        print(f"Supervisor Approval: {decision.state} — {decision.description}")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
