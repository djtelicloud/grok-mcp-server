import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..utils import (
    PathResolver,
    communicate_with_timeout,
    get_unigrok_runtime,
    is_cloudrun_runtime,
    register_internal_tool,
)


# Spec ToolAnnotations for MCP clients: reads never touch the working tree;
# commit/apply-patch mutate it in ways that are not trivially reversible.
READONLY_TOOL = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE_TOOL = ToolAnnotations(destructiveHint=True)

PATCH_LIMIT_BYTES = 512 * 1024
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-~^]{0,180}$")


def _git_read_unavailable() -> Optional[str]:
    if is_cloudrun_runtime():
        return "Git read tools are unavailable in Cloud Run runtime."
    return None


def _check_write_allowed():
    if get_unigrok_runtime() != "local" or os.environ.get("ENABLE_GIT_WRITE") != "1":
        raise PermissionError("Git write tools require UNIGROK_RUNTIME=local and ENABLE_GIT_WRITE=1.")


def _validate_branch_name(branch_name: str) -> str:
    value = branch_name.strip()
    invalid = (
        not BRANCH_RE.match(value)
        or ".." in value
        or "@{" in value
        or value.endswith("/")
        or value.endswith(".")
        or value.endswith(".lock")
        or "//" in value
    )
    if invalid:
        raise ValueError(f"Invalid branch name: {branch_name}")
    return value


def _validate_ref(ref: str) -> str:
    value = ref.strip()
    if not REF_RE.match(value) or ".." in value or "@{" in value:
        raise ValueError(f"Invalid git ref: {ref}")
    return value


def _repo_root(repo_path: Optional[str] = None) -> Path:
    if repo_path:
        return PathResolver.validate_path(repo_path)
    return PathResolver.get_project_root().resolve()


def _validate_repo_path(path_value: str, repo: Path) -> str:
    candidate = Path(path_value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (repo / candidate).resolve()
    PathResolver.validate_path(str(resolved))
    try:
        return str(resolved.relative_to(repo.resolve()))
    except ValueError:
        raise PermissionError(f"Path is outside git repository: {path_value}")


def _extract_patch_file_header(line: str) -> str:
    raw = line[4:].strip()
    if not raw:
        raise ValueError("Patch contains an empty file target.")
    if raw == "/dev/null":
        return raw
    if raw.startswith('"'):
        parts = shlex.split(raw)
        if not parts:
            raise ValueError("Patch contains an empty file target.")
        return parts[0]
    if "\t" in raw:
        return raw.split("\t", 1)[0].strip()
    if " " in raw:
        raise ValueError(f"Patch path is ambiguous: {raw}")
    return raw


def _normalize_patch_target(path_value: str, repo: Path) -> Optional[str]:
    value = path_value.strip()
    if not value:
        raise ValueError("Patch contains an empty file target.")
    if value == "/dev/null":
        return None
    if Path(value).is_absolute():
        raise PermissionError(f"Patch target must be relative: {path_value}")
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    if not value:
        raise ValueError("Patch contains an empty file target.")
    if Path(value).is_absolute():
        raise PermissionError(f"Patch target must be relative: {path_value}")
    return _validate_repo_path(value, repo)


def _validate_patch_targets(patch: str, repo: Path):
    targets: List[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                raise ValueError(f"Malformed diff header: {line}") from exc
            if len(parts) != 4:
                raise ValueError(f"Malformed diff header: {line}")
            targets.extend(parts[2:4])
        elif line.startswith("--- ") or line.startswith("+++ "):
            targets.append(_extract_patch_file_header(line))

    for target in targets:
        _normalize_patch_target(target, repo)


async def _run_git(args: List[str], repo_path: Optional[str] = None, stdin: Optional[bytes] = None) -> str:
    repo = _repo_root(repo_path)
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(repo),
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = float(os.getenv("UNIGROK_GIT_TIMEOUT", "15.0"))
    stdout, stderr = await communicate_with_timeout(proc, timeout, stdin)
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError((err or out or f"git {' '.join(args)} failed").strip())
    return out.strip()


async def git_status(repo_path: Optional[str] = None) -> str:
    """Return `git status --porcelain` for the current repository."""
    if msg := _git_read_unavailable():
        return msg
    output = await _run_git(["status", "--porcelain"], repo_path)
    return output or "Working tree clean."


async def git_diff(cached: bool = False, path: Optional[str] = None, repo_path: Optional[str] = None) -> str:
    """Return the current git diff, optionally for staged changes or one path."""
    if msg := _git_read_unavailable():
        return msg
    repo = _repo_root(repo_path)
    args = ["diff", "--cached" if cached else "--"]
    if cached:
        args = ["diff", "--cached", "--"]
    if path:
        args.append(_validate_repo_path(path, repo))
    output = await _run_git(args, str(repo))
    return output or "No diff."


async def git_log(limit: int = 10, repo_path: Optional[str] = None) -> str:
    """Return a short one-line git history."""
    if msg := _git_read_unavailable():
        return msg
    safe_limit = min(max(int(limit), 1), 100)
    return await _run_git(["log", "-n", str(safe_limit), "--oneline"], repo_path)


async def git_show(commit: str = "HEAD", repo_path: Optional[str] = None) -> str:
    """Return `git show` for a validated commit-ish ref."""
    if msg := _git_read_unavailable():
        return msg
    return await _run_git(["show", "--stat", "--patch", _validate_ref(commit)], repo_path)


async def git_current_branch(repo_path: Optional[str] = None) -> str:
    """Return the active branch name."""
    if msg := _git_read_unavailable():
        return msg
    return await _run_git(["branch", "--show-current"], repo_path)


async def git_create_branch(branch_name: str, repo_path: Optional[str] = None) -> str:
    """Create and switch to a new branch. Requires local git write mode."""
    _check_write_allowed()
    safe_branch = _validate_branch_name(branch_name)
    await _run_git(["checkout", "-b", safe_branch], repo_path)
    return f"Created and switched to branch `{safe_branch}`."


async def git_apply_patch(patch: str, repo_path: Optional[str] = None) -> str:
    """Apply a unified diff patch. Requires local git write mode."""
    _check_write_allowed()
    payload = patch.encode("utf-8")
    if len(payload) > PATCH_LIMIT_BYTES:
        raise ValueError("Patch exceeds 512KB limit.")
    repo = _repo_root(repo_path)
    _validate_patch_targets(patch, repo)
    await _run_git(["apply", "--check", "-"], repo_path, stdin=payload)
    await _run_git(["apply", "-"], repo_path, stdin=payload)
    return "Patch applied successfully."


async def git_commit(message: str, paths: List[str], repo_path: Optional[str] = None) -> str:
    """Commit explicit paths only. Requires local git write mode."""
    _check_write_allowed()
    if not message.strip():
        raise ValueError("Commit message is required.")
    if not paths:
        raise ValueError("At least one explicit path is required.")
    repo = _repo_root(repo_path)
    safe_paths = [_validate_repo_path(path, repo) for path in paths]
    await _run_git(["add", "--", *safe_paths], str(repo))
    output = await _run_git(["commit", "-m", message.strip(), "--", *safe_paths], str(repo))
    return output or "Commit created."


def register_git_tools(mcp: FastMCP):
    mcp.add_tool(git_status, annotations=READONLY_TOOL)
    mcp.add_tool(git_diff, annotations=READONLY_TOOL)
    mcp.add_tool(git_log, annotations=READONLY_TOOL)
    mcp.add_tool(git_show, annotations=READONLY_TOOL)
    mcp.add_tool(git_current_branch, annotations=READONLY_TOOL)
    mcp.add_tool(git_create_branch)
    mcp.add_tool(git_apply_patch, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(git_commit, annotations=DESTRUCTIVE_TOOL)


register_internal_tool("git_status", git_status)
register_internal_tool("git_diff", git_diff)
register_internal_tool("git_log", git_log)
register_internal_tool("git_show", git_show)
register_internal_tool("git_current_branch", git_current_branch)
register_internal_tool("git_create_branch", git_create_branch)
register_internal_tool("git_apply_patch", git_apply_patch)
register_internal_tool("git_commit", git_commit)
