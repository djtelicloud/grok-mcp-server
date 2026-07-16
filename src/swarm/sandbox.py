"""Per-task evaluation sandbox for swarm candidates.

One work dir per task: a one-time copy of the attached workspace with the
project's `.venv` symlinked in (never copied — `uv run` against a venv-less
copy would resolve a fresh environment, and a bare interpreter would miss
project deps). The work dir is PREPENDED to PYTHONPATH so the patched tree
shadows any editable-install `.pth` that points at the original workspace;
preflight's import-provenance probe proves the shadowing actually worked.

Mutant test/bench children are UNTRUSTED code: they run in their own session
(process-group SIGKILL on timeout), under RLIMIT_AS/RLIMIT_CPU, with an
env built from an allowlist so `XAI_API_KEY` and friends are never
inherited. Network denial is not achievable without root — documented
residual risk; the env scrub removes the credentials that would make
exfiltration valuable.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..subprocess_security import (
    create_scrubbed_subprocess_exec,
    scrubbed_subprocess_run,
)

_EXCLUDED_DIRS = {
    ".git", ".venv", "chats", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
    ".claude",
}
_ENV_ALLOWLIST = {
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "USER", "SHELL",
    "UNI_GROK_TESTING", "PYTHONIOENCODING", "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
}
_BENCH_MARKER = "SWARM_BENCH "
_OUTPUT_CAP = 20000


class SandboxError(RuntimeError):
    """Sandbox setup or evaluation infrastructure failure (not a candidate
    verdict): copy guard trips, missing target, malformed bench output."""


def parse_bench_line(stdout: str) -> Optional[Dict[str, float]]:
    """Extract the single `SWARM_BENCH {...}` contract line; None when the
    contract is not met (missing, duplicated, or malformed)."""
    lines = [line for line in (stdout or "").splitlines() if line.startswith(_BENCH_MARKER)]
    if len(lines) != 1:
        return None
    try:
        payload = json.loads(lines[0][len(_BENCH_MARKER):])
        latency = float(payload["latency_ms"])
        peak = float(payload["peak_mem_bytes"])
    except (KeyError, TypeError, ValueError):
        return None
    if latency < 0 or peak < 0:
        return None
    return {"latency_ms": latency, "peak_mem_bytes": peak}


class SwarmSandbox:
    def __init__(
        self,
        workspace_root: Path,
        work_root: Path,
        target_rel: str,
        max_copy_mb: int = 500,
        max_copy_files: int = 20000,
        child_mem_mb: int = 2048,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.work_root = Path(work_root)
        self.work = self.work_root / "work"
        self.target_rel = str(target_rel)
        self.max_copy_bytes = int(max_copy_mb) * 1024 * 1024
        self.max_copy_files = int(max_copy_files)
        self.child_mem_bytes = int(child_mem_mb) * 1024 * 1024
        self._python_bin: Optional[str] = None

    # ── Setup / teardown ─────────────────────────────────────────────────────

    def create(self) -> None:
        """Copy the workspace (bounded, byte-exact, symlink-safe) and link
        the original venv in."""
        if self.work.exists():
            shutil.rmtree(self.work)
        self.work.mkdir(parents=True)
        copied_bytes = 0
        copied_files = 0
        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
            rel_root = Path(root).relative_to(self.workspace_root)
            for name in files:
                src = Path(root) / name
                if src.is_symlink():
                    # Only in-workspace symlink targets are followed; links
                    # escaping the workspace are silently skipped.
                    try:
                        resolved = src.resolve()
                        resolved.relative_to(self.workspace_root)
                    except (OSError, ValueError):
                        continue
                try:
                    size = src.stat().st_size
                except OSError:
                    raise SandboxError(f"cannot stat {src} while copying workspace")
                copied_bytes += size
                copied_files += 1
                if copied_bytes > self.max_copy_bytes:
                    raise SandboxError(
                        f"workspace exceeds the copy guard "
                        f"({self.max_copy_bytes // (1024 * 1024)}MB); raise "
                        "UNIGROK_SWARM_MAX_COPY_MB deliberately if intended"
                    )
                if copied_files > self.max_copy_files:
                    raise SandboxError("workspace exceeds the copy file-count guard")
                dest = self.work / rel_root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                # copyfile preserves bytes exactly — no newline normalization.
                shutil.copyfile(src, dest)
        venv = self.workspace_root / ".venv"
        if venv.is_dir():
            (self.work / ".venv").symlink_to(venv)
        self._python_bin = self._select_python_bin()
        if not self.target_path.is_file():
            raise SandboxError(f"target {self.target_rel!r} missing from workspace copy")

    def destroy(self) -> None:
        shutil.rmtree(self.work_root, ignore_errors=True)

    # ── Target file plumbing ─────────────────────────────────────────────────

    @property
    def target_path(self) -> Path:
        return self.work / self.target_rel

    def read_target(self) -> bytes:
        return self.target_path.read_bytes()

    def write_target(self, data: bytes) -> None:
        self.target_path.write_bytes(data)

    def hygiene(self) -> None:
        """Per-candidate cleanup so one mutant's cache pollution can't skew
        the next one's feasibility or bench numbers."""
        for name in (".pytest_cache", ".coverage", "coverage.json"):
            path = self.work / name
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink(missing_ok=True)
        pycache = self.target_path.parent / "__pycache__"
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)

    # ── Child processes ──────────────────────────────────────────────────────

    def python_bin(self) -> str:
        if self._python_bin is not None:
            return self._python_bin
        self._python_bin = self._select_python_bin()
        return self._python_bin

    def _select_python_bin(self) -> str:
        """Use the project venv only when it executes on this runtime.

        A macOS worktree is commonly mounted into the Linux contributor
        container. Its `.venv/bin/python` exists but is not a Linux binary;
        blindly selecting it makes every preflight fail before model work.
        """
        venv_python = self.work / ".venv" / "bin" / "python"
        if venv_python.is_file():
            try:
                probe = scrubbed_subprocess_run(
                    [str(venv_python), "-c", "import sys; raise SystemExit(0)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                if probe.returncode == 0:
                    return str(venv_python)
            except (OSError, subprocess.SubprocessError):
                pass
        return sys.executable

    def child_env(self) -> Dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in _ENV_ALLOWLIST
        }
        private_home = self.work_root / "home"
        private_tmp = self.work_root / "tmp"
        private_home.mkdir(parents=True, exist_ok=True)
        private_tmp.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(private_home)
        env["TMPDIR"] = str(private_tmp)
        env["PYTHONPATH"] = str(self.work)
        env["PYTHONHASHSEED"] = "0"
        return env

    def _child_limits(self):
        mem = self.child_mem_bytes

        def _apply():
            import resource

            for limit, value in (
                (resource.RLIMIT_AS, mem),
                (resource.RLIMIT_CPU, 600),
            ):
                try:
                    resource.setrlimit(limit, (value, value))
                except (ValueError, OSError):
                    pass  # platform-dependent (RLIMIT_AS is advisory on macOS)

        return _apply

    async def run_child(
        self, argv: List[str], timeout: float
    ) -> Tuple[int, str, str]:
        """Run one untrusted child: own session, RLIMITs, allowlisted env,
        process-group SIGKILL on timeout (rc -9)."""
        proc = await create_scrubbed_subprocess_exec(
            *argv,
            cwd=str(self.work),
            env=self.child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=self._child_limits(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            await proc.wait()
            return -9, "", f"killed after {timeout:.0f}s timeout"
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace")[:_OUTPUT_CAP],
            stderr.decode("utf-8", errors="replace")[:_OUTPUT_CAP],
        )

    async def run_tests(self, test_target: str, timeout: float) -> Tuple[bool, str]:
        rc, out, err = await self.run_child(
            [self.python_bin(), "-m", "pytest", "-q", "-p", "no:cacheprovider", test_target],
            timeout,
        )
        return rc == 0, (out + ("\n" + err if err else ""))[:_OUTPUT_CAP]

    async def run_bench(
        self, bench_argv: List[str], repeats: int, timeout: float
    ) -> Dict[str, Any]:
        """1 discarded warmup + `repeats` measured runs of the SWARM_BENCH
        contract command; medians + raw samples (for noise-floor math).
        Raises SandboxError when the command fails or breaks the contract —
        for the BASELINE that fails the task; for a mutant the engine treats
        it as an infeasible candidate at the bench stage."""
        samples: List[Dict[str, float]] = []
        for attempt in range(int(repeats) + 1):
            rc, out, err = await self.run_child(list(bench_argv), timeout)
            if rc != 0:
                raise SandboxError(f"bench_command exited {rc}: {err[:500] or out[:500]}")
            parsed = parse_bench_line(out)
            if parsed is None:
                raise SandboxError(
                    "bench_command must print exactly one 'SWARM_BENCH {\"latency_ms\":..,"
                    "\"peak_mem_bytes\":..}' line"
                )
            if attempt == 0:
                continue  # warmup discarded
            samples.append(parsed)
        latencies = [s["latency_ms"] for s in samples]
        peaks = [s["peak_mem_bytes"] for s in samples]
        return {
            "latency_ms": statistics.median(latencies),
            "peak_mem_bytes": int(statistics.median(peaks)),
            "latency_samples": latencies,
        }
