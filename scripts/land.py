#!/usr/bin/env python3
"""Safely land one verified agent worktree onto the visible local main."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from urllib.request import Request, urlopen


MAIN_REF = "refs/heads/main"
DEFAULT_TEST_ARGS = ["uv", "run", "pytest", "-q"]
DEV_COMPOSE_FILE = "docker-compose.dev.yml"
DEV_BASE_URL = "http://127.0.0.1:4766"


class LandError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise LandError(f"{' '.join(args)}: {detail}")
    return result


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check).stdout.strip()


def worktrees(repo: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        records.append(current)
    return records


def main_worktree(repo: Path) -> Path:
    matches = [Path(item["worktree"]) for item in worktrees(repo) if item.get("branch") == MAIN_REF]
    if len(matches) != 1:
        raise LandError(f"expected exactly one checked-out main worktree, found {len(matches)}")
    return matches[0]


def common_git_dir(repo: Path) -> Path:
    raw = Path(git(repo, "rev-parse", "--git-common-dir"))
    return raw if raw.is_absolute() else (repo / raw).resolve()


def require_clean(repo: Path, *, include_untracked: bool, label: str) -> None:
    args = ["status", "--porcelain=v1"]
    if not include_untracked:
        args.append("--untracked-files=no")
    dirty = git(repo, *args)
    if dirty:
        raise LandError(f"{label} is dirty; refusing to overwrite or stash anything:\n{dirty}")


def pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def directory_lock(path: Path, *, timeout: float = 900.0) -> Iterator[None]:
    deadline = time.monotonic() + timeout
    owner_path = path / "owner.json"
    while True:
        try:
            path.mkdir()
            owner_path.write_text(
                json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started": time.time()}),
                encoding="utf-8",
            )
            break
        except FileExistsError:
            try:
                owner = json.loads(owner_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                owner = {}
            if (
                owner.get("host") == socket.gethostname()
                and isinstance(owner.get("pid"), int)
                and not pid_is_live(owner["pid"])
            ):
                try:
                    owner_path.unlink(missing_ok=True)
                    path.rmdir()
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise LandError(f"timed out waiting for shared landing lock {path}")
            time.sleep(0.25)
    try:
        yield
    finally:
        owner_path.unlink(missing_ok=True)
        try:
            path.rmdir()
        except OSError:
            pass


def run_tests(repo: Path, expected_head: str) -> None:
    print("Checking human and agent attribution...", flush=True)
    run(
        [
            "uv",
            "run",
            "python",
            "scripts/check_agent_attribution.py",
            "--base-ref",
            MAIN_REF,
            "--head",
            expected_head,
        ],
        cwd=repo,
        capture=False,
    )
    print("Checking deterministic OKF bundle...", flush=True)
    run(
        ["uv", "run", "python", "scripts/generate_okf.py", "--check"],
        cwd=repo,
        capture=False,
    )
    command = DEFAULT_TEST_ARGS
    print(f"Testing {expected_head[:12]}: {' '.join(command)}", flush=True)
    result = run(command, cwd=repo, check=False, capture=False)
    if result.returncode:
        raise LandError(f"tests failed with exit code {result.returncode}")
    if git(repo, "rev-parse", "HEAD") != expected_head:
        raise LandError("worktree HEAD changed while tests were running")
    require_clean(repo, include_untracked=True, label="agent worktree after tests")


def changed_paths(repo: Path, old: str, new: str) -> list[str]:
    if old == new:
        return []
    return [line for line in git(repo, "diff", "--name-only", f"{old}..{new}").splitlines() if line]


def runtime_action(paths: list[str]) -> str:
    rebuild_names = {"Dockerfile", "docker-compose.yml", "docker-compose.dev.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "pyproject.toml", "uv.lock"}
    if any(path in rebuild_names or path.startswith("docker/") for path in paths):
        return "rebuild"
    if any(
        path == "main.py"
        or path.startswith("src/")
        or path.startswith("mcp_ui/")
        or path.startswith("docs/okf/")
        for path in paths
    ):
        return "rebuild"
    return "none"


def get_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:  # noqa: S310 - fixed loopback URLs only
        return json.load(response)


def probe_json(url: str, expected_status: str) -> None:
    payload = get_json(url)
    if payload.get("status") != expected_status:
        raise LandError(f"unexpected response from {url}: {payload}")


def probe_ui(base_url: str) -> None:
    with urlopen(f"{base_url}/ui/", timeout=10) as response:  # noqa: S310
        body = response.read(4096)
    if b"UniGrok" not in body:
        raise LandError("Control Center smoke check did not find the UniGrok marker")


def probe_okf(base_url: str) -> None:
    manifest = get_json(f"{base_url}/docs/okf/okf-manifest.json")
    files = manifest.get("files")
    if not isinstance(files, list) or "api-reference.md" not in files:
        raise LandError("OKF manifest does not expose api-reference.md")
    for file_name in files:
        if not isinstance(file_name, str) or "/" in file_name or file_name.startswith("."):
            raise LandError(f"unsafe OKF manifest entry: {file_name!r}")
        with urlopen(f"{base_url}/docs/okf/{file_name}", timeout=10) as response:  # noqa: S310
            if response.status != 200:
                raise LandError(f"OKF document returned HTTP {response.status}: {file_name}")


def configured_client_token(repo: Path) -> Optional[str]:
    env_path = repo / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "UNIGROK_API_KEYS":
            return next((part.strip() for part in value.strip().strip('"\'').split(",") if part.strip()), None)
    return None


def probe_mcp(base_url: str, token: Optional[str] = None) -> None:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "unigrok-land", "version": "1"},
            },
        }
    ).encode()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-Client-ID": "unigrok-land",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url}/mcp",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310
        body = response.read().decode("utf-8", errors="replace")
    if "protocolVersion" not in body or '"result"' not in body:
        raise LandError("MCP initialize smoke check returned an unexpected payload")


def wait_for_runtime(repo: Path, *, base_url: str, timeout: float = 60.0) -> None:
    """Wait through the normal Docker restart window before failing smoke.

    Uvicorn can close the first connection while the old process exits even
    after ``docker compose restart`` returns. A landing receipt must reflect
    eventual readiness, not a race with that expected handoff.
    """
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            probe_json(f"{base_url}/healthz", "healthy")
            probe_json(f"{base_url}/readyz", "ready")
            probe_ui(base_url)
            probe_okf(base_url)
            runtime = get_json(f"{base_url}/runtimez")
            token = configured_client_token(repo) if runtime.get("gateway_auth", {}).get("enabled") else None
            if runtime.get("gateway_auth", {}).get("enabled") and not token:
                raise LandError(
                    "MCP auth is enabled but no client token is available for the landing smoke check"
                )
            probe_mcp(base_url, token)
            return
        except Exception as exc:  # expected briefly while Docker hands off
            last_error = exc
            time.sleep(0.5)
    raise LandError(f"runtime did not become ready within {timeout:.0f}s: {last_error}")


def reconcile_runtime(repo: Path, paths: list[str]) -> str:
    action = runtime_action(paths)
    if action == "none":
        return "unchanged"
    compose = run(
        ["docker", "compose", "-p", "grok-mcp-dev", "-f", DEV_COMPOSE_FILE, "ps", "--status", "running", "--services"],
        cwd=repo,
        check=False,
    )
    if compose.returncode:
        raise LandError((compose.stderr or "cannot inspect Docker Compose").strip())
    if "grok-mcp" not in compose.stdout.split():
        return "contributor dev service not running; stable service untouched"
    if action == "rebuild":
        run(
            ["docker", "compose", "-p", "grok-mcp-dev", "-f", DEV_COMPOSE_FILE, "up", "--build", "-d", "grok-mcp"],
            cwd=repo,
            capture=False,
        )
    elif action == "restart":
        run(
            ["docker", "compose", "-p", "grok-mcp-dev", "-f", DEV_COMPOSE_FILE, "restart", "grok-mcp"],
            cwd=repo,
            capture=False,
        )
    base_url = os.environ.get("UNIGROK_LAND_DEV_URL", DEV_BASE_URL).rstrip("/")
    wait_for_runtime(repo, base_url=base_url)
    if action == "rebuild":
        return "rebuilt and smoke-tested"
    if action == "restart":
        return "restarted and smoke-tested"
    return "smoke-tested"


def write_receipt(
    git_dir: Path,
    *,
    head: str,
    branch: str,
    main_path: Path,
    previous_main: str,
    changed_paths: list[str],
) -> None:
    receipts = git_dir / "unigrok-land" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts / f"{head}.json"
    if receipt_path.exists():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise LandError(f"existing landing receipt is corrupt: {receipt_path}") from exc
        existing_tests = existing.get("tests")
        if existing.get("head") != head or (
            existing_tests and existing_tests.get("status") != "passed"
        ):
            raise LandError(f"existing landing receipt is not a valid success receipt: {receipt_path}")
        # Successful receipts are content-addressed provenance. Re-landing an
        # already-landed commit must never change its receipt hash.
        return
    receipt_path.write_text(
        json.dumps(
            {
                "head": head,
                "branch": branch,
                "previous_main": previous_main,
                "changed_paths": changed_paths,
                "main_worktree": str(main_path),
                "test_command": DEFAULT_TEST_ARGS,
                "tests": {"command": DEFAULT_TEST_ARGS, "status": "passed"},
                "landed_at": time.time(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def runtime_changes(
    git_dir: Path, repo: Path, *, fallback: str, target: str
) -> tuple[list[str], Path, str]:
    state_dir = git_dir / "unigrok-land"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / "runtime-head"
    start = fallback
    try:
        candidate = marker.read_text(encoding="utf-8").strip()
    except OSError:
        candidate = ""
    if candidate:
        valid = run(["git", "cat-file", "-e", f"{candidate}^{{commit}}"], cwd=repo, check=False)
        target_ancestor = run(
            ["git", "merge-base", "--is-ancestor", candidate, target],
            cwd=repo,
            check=False,
        )
        fallback_descendant = run(
            ["git", "merge-base", "--is-ancestor", candidate, fallback],
            cwd=repo,
            check=False,
        )
        if (
            valid.returncode == 0
            and target_ancestor.returncode == 0
            and fallback_descendant.returncode == 0
        ):
            start = candidate
        else:
            marker.write_text(fallback + "\n", encoding="utf-8")
    else:
        # Persist the known-good pre-landing runtime point before any restart
        # or rebuild. If reconciliation fails after main moves, the next run
        # still knows the full range that remains to be applied.
        marker.write_text(fallback + "\n", encoding="utf-8")
    return changed_paths(repo, start, target), marker, start


def land(repo: Path) -> str:
    repo = Path(git(repo, "rev-parse", "--show-toplevel"))
    branch = git(repo, "symbolic-ref", "--short", "HEAD")
    if branch == "main":
        raise LandError("run scripts/land from an agent task worktree, never from shared main")
    if not branch.startswith("codex/"):
        raise LandError(
            "only a Codex-owned integration branch may run scripts/land; "
            "contributors must hand off their exact commit for PR review"
        )
    require_clean(repo, include_untracked=True, label="agent worktree")
    main_path = main_worktree(repo)
    common_dir = common_git_dir(repo)
    baseline = git(main_path, "rev-parse", "HEAD")
    ancestor = run(
        ["git", "merge-base", "--is-ancestor", baseline, "HEAD"],
        cwd=repo,
        check=False,
    )
    if ancestor.returncode:
        raise LandError(
            "reviewed branch is behind current main; rebase, publish the new head, "
            "and obtain exact-head review before landing"
        )

    tested_head = git(repo, "rev-parse", "HEAD")
    run_tests(repo, tested_head)

    with directory_lock(common_dir / "unigrok-land.lock"):
        current_main = git(main_path, "rev-parse", "HEAD")
        if current_main != baseline:
            raise LandError(
                "main advanced while tests ran; rebase the reviewed branch onto current main, "
                "publish the new head, and obtain exact-head review before landing"
            )
        require_clean(main_path, include_untracked=True, label="shared main worktree")
        run(["git", "merge", "--ff-only", tested_head], cwd=main_path, capture=False)
        if git(main_path, "rev-parse", "HEAD") != tested_head:
            raise LandError("shared main did not reach the tested commit")
        paths, runtime_marker, certified_base = runtime_changes(
            common_dir,
            main_path,
            fallback=baseline,
            target=tested_head,
        )
        runtime = reconcile_runtime(main_path, paths)
        runtime_marker.write_text(tested_head + "\n", encoding="utf-8")
        # The receipt is the workspace-memory trust boundary. Write it
        # only after tests, fast-forward, and runtime reconciliation all
        # succeed — exactly when this command can emit LANDED TO MAIN.
        write_receipt(
            common_dir,
            head=tested_head,
            branch=branch,
            main_path=main_path,
            previous_main=certified_base,
            changed_paths=paths,
        )
    print(f"Runtime: {runtime}", flush=True)
    return tested_head


def main() -> int:
    print(
        "NOT LANDED: local landing is disabled until verification runs outside "
        "candidate and shared Git control planes",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
