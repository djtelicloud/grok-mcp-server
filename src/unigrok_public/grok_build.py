from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_JSON_LINE_BYTES = 2_000_000
# Grok Build >= 0.2.101 uses lowercase tool IDs; keep legacy PascalCase for older binaries.
LOCAL_AUTHORITY_TOOLS = (
    "run_terminal_command,run_terminal_cmd,bash,Bash,"
    "read_file,Read,search_replace,Edit,Write,write_file,"
    "grep,Grep,list_dir,Glob,glob,task,Task,MCPTool,mcp_tool"
)
TOOLLESS_RETRY_PROMPT = (
    "Local tool execution is unavailable in this environment and your tool call was "
    "rejected by policy. Do not call any tools. Using pure reasoning only, produce "
    "your complete final answer to the previous request now."
)


def _normalize_stop_reason(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace("-", "_")
    if text in {"endturn", "end_turn", "stop"}:
        return "end_turn"
    if text in {"cancelled", "canceled"}:
        return "cancelled"
    return text or "unknown"


def _permission_reject_result(options: Any) -> dict[str, Any]:
    """Return an ACP permission result that rejects local authority without cancelling."""
    items = [item for item in (options or []) if isinstance(item, dict)]
    by_kind = {
        str(item.get("kind") or "").strip().lower().replace("-", "_"): item for item in items
    }
    for kind in ("reject_always", "reject_once"):
        item = by_kind.get(kind)
        if item is not None and item.get("optionId") is not None:
            return {
                "outcome": {
                    "outcome": "selected",
                    "optionId": item.get("optionId"),
                }
            }
    for item in items:
        blob = f"{item.get('kind') or ''} {item.get('optionId') or ''} {item.get('name') or ''}"
        if "reject" in blob.lower() and item.get("optionId") is not None:
            return {
                "outcome": {
                    "outcome": "selected",
                    "optionId": item.get("optionId"),
                }
            }
    # Last resort: cancelled ends the turn; prefer reject options above.
    return {"outcome": {"outcome": "cancelled"}}


@dataclass
class _TurnState:
    text_runs: list[str] = field(default_factory=list)
    current_text: list[str] = field(default_factory=list)

    def boundary(self) -> None:
        if not self.current_text:
            return
        text = "".join(self.current_text).strip()
        if text:
            self.text_runs.append(text)
        self.current_text.clear()

    def update(self, update: dict[str, Any]) -> None:
        if update.get("sessionUpdate") == "agent_message_chunk":
            content = update.get("content")
            text = content.get("text") if isinstance(content, dict) else None
            if isinstance(text, str):
                self.current_text.append(text)
            return
        # Thoughts, tool calls, and lifecycle updates separate preliminary
        # narration from the final answer. Their contents never leave this plane.
        self.boundary()

    def final_text(self) -> str:
        self.boundary()
        return self.text_runs[-1] if self.text_runs else ""


class GrokBuildWorker:
    """One persistent, isolated Grok Build ACP connection.

    A worker may multiplex multiple ACP sessions. The upstream Grok Build service
    remains the authority for subscription concurrency and usage limits.
    """

    def __init__(
        self,
        *,
        binary: str,
        auth_path: Path,
        model: str | None,
        effort: str | None,
        max_turns: int,
        allow_web: bool,
        agentic: bool,
        system_prompt: str,
        timeout_seconds: int,
    ) -> None:
        self.binary = binary
        self.auth_path = auth_path
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.allow_web = allow_web
        self.agentic = agentic
        self.system_prompt = system_prompt
        self.timeout_seconds = timeout_seconds
        self.process: asyncio.subprocess.Process | None = None
        self._runtime: tempfile.TemporaryDirectory[str] | None = None
        self._work: Path | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._turns: dict[str, _TurnState] = {}
        self._next_id = 1
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = ""
        self.started_at: float | None = None
        self.completed_turns = 0
        self.in_flight = 0
        self._ready = False

    def _runtime_env(self) -> tuple[Path, dict[str, str]]:
        if not self.auth_path.is_file():
            raise RuntimeError("Grok Build authentication is not initialized")
        self._runtime = tempfile.TemporaryDirectory(prefix="unigrok-build-acp-")
        root = Path(self._runtime.name)
        directories = {
            name: root / name
            for name in (
                "home",
                "work",
                "grok-home",
                "tmp",
                "xdg-config",
                "xdg-data",
                "xdg-cache",
            )
        }
        for directory in directories.values():
            directory.mkdir(mode=0o700)
        (directories["grok-home"] / "config.toml").write_text(
            "[cli]\nauto_update = false\n", encoding="utf-8"
        )
        allowed = {
            "PATH",
            "LANG",
            "LANGUAGE",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update(
            {
                "HOME": str(directories["home"]),
                "PWD": str(directories["work"]),
                "TMPDIR": str(directories["tmp"]),
                "GROK_HOME": str(directories["grok-home"]),
                "GROK_AUTH_PATH": str(self.auth_path),
                "XDG_CONFIG_HOME": str(directories["xdg-config"]),
                "XDG_DATA_HOME": str(directories["xdg-data"]),
                "XDG_CACHE_HOME": str(directories["xdg-cache"]),
                "NO_COLOR": "1",
            }
        )
        self._work = directories["work"]
        return directories["work"], env

    def _command(self) -> list[str]:
        args = [
            self.binary,
            "--no-auto-update",
            "--no-memory",
            "--no-subagents",
            "--permission-mode",
            "dontAsk",
            "--no-plan",
            "--system-prompt-override",
            self.system_prompt,
            # Remove local-authority tools from the model surface. Denied-but-visible
            # tools can still be selected, which ends a dontAsk ACP turn as cancelled.
            "--disallowed-tools",
            LOCAL_AUTHORITY_TOOLS,
            "--max-turns",
            str(self.max_turns),
        ]
        if self.model:
            args.extend(["-m", self.model])
        if self.effort:
            args.extend(["--effort", self.effort])
        if not self.agentic or not self.allow_web:
            args.extend(["--disable-web-search", "--tools", ""])
        if not self.agentic:
            # Verbatim + empty tools keeps chat/fast turns off the permission path.
            args.append("--verbatim")
        args.extend(["agent", "stdio"])
        return args

    async def start(self) -> None:
        async with self._start_lock:
            if (
                self._ready
                and self.process is not None
                and self.process.returncode is None
            ):
                return
            try:
                work, env = self._runtime_env()
                self.process = await asyncio.create_subprocess_exec(
                    *self._command(),
                    cwd=str(work),
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=MAX_JSON_LINE_BYTES,
                )
                self.started_at = time.monotonic()
                self._reader_task = asyncio.create_task(self._read_stdout())
                self._stderr_task = asyncio.create_task(self._read_stderr())
                initialized = await self._request(
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientCapabilities": {
                            "fs": {"readTextFile": False, "writeTextFile": False},
                            "terminal": False,
                        },
                    },
                    deadline_seconds=15,
                )
                auth_methods = {
                    str(item.get("id"))
                    for item in initialized.get("authMethods", [])
                    if isinstance(item, dict)
                }
                if "cached_token" not in auth_methods:
                    raise RuntimeError(
                        "Grok Build cached OAuth authentication is unavailable"
                    )
                await self._request(
                    "authenticate",
                    {"methodId": "cached_token", "_meta": {"headless": True}},
                    deadline_seconds=15,
                )
                self._ready = True
            except Exception:
                # Never leave a live-but-unusable process in the worker cache.
                self._ready = False
                await self.close()
                raise

    async def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise RuntimeError("Grok Build ACP runtime is unavailable")
        encoded = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    async def _request(
        self, method: str, params: dict[str, Any], *, deadline_seconds: float
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        try:
            message = await asyncio.wait_for(future, timeout=deadline_seconds)
        finally:
            self._pending.pop(request_id, None)
        if "error" in message:
            error = message.get("error")
            detail = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(f"Grok Build ACP request failed: {detail or 'unknown error'}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    async def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            raise RuntimeError("Grok Build ACP stdout is unavailable")
        try:
            while line := await process.stdout.readline():
                if len(line) > MAX_JSON_LINE_BYTES:
                    raise RuntimeError("Grok Build ACP event exceeded the output limit")
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                method = message.get("method")
                if isinstance(request_id, int) and not method:
                    future = self._pending.get(request_id)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                if method == "session/update":
                    params = message.get("params")
                    if not isinstance(params, dict):
                        continue
                    session_id = str(params.get("sessionId") or "")
                    update = params.get("update")
                    state = self._turns.get(session_id)
                    if state is not None and isinstance(update, dict):
                        state.update(update)
                elif method == "session/request_permission" and isinstance(request_id, int):
                    # The gateway grants no local authority: select reject_* when offered
                    # so the model can continue; only cancel when no reject option exists.
                    params = message.get("params")
                    options = (
                        params.get("options") if isinstance(params, dict) else None
                    ) or []
                    await self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": _permission_reject_result(options),
                        }
                    )
                elif isinstance(request_id, int) and method:
                    await self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "Method not supported"},
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(RuntimeError("Grok Build ACP stream failed"))
            if self.process is not None and self.process.returncode is None:
                self.process.kill()
            self._stderr_tail = (self._stderr_tail + f" {type(exc).__name__}")[-1000:]
        finally:
            self._fail_pending(RuntimeError("Grok Build ACP runtime exited"))

    async def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            raise RuntimeError("Grok Build ACP stderr is unavailable")
        try:
            while chunk := await process.stderr.read(4096):
                self._stderr_tail = (self._stderr_tail + chunk.decode(errors="replace"))[-1000:]
        except asyncio.CancelledError:
            raise

    def _fail_pending(self, error: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    async def run(self, prompt: str) -> dict[str, Any]:
        await self.start()
        if self._work is None:
            raise RuntimeError("Grok Build ACP workspace is unavailable")
        created = await self._request(
            "session/new",
            {"cwd": str(self._work), "mcpServers": []},
            deadline_seconds=15,
        )
        session_id = str(created.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("Grok Build ACP did not create a session")
        state = _TurnState()
        self._turns[session_id] = state
        self.in_flight += 1
        started = time.monotonic()
        try:
            result = await self._request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
                deadline_seconds=self.timeout_seconds,
            )
            first_stop = _normalize_stop_reason(result.get("stopReason"))
            if first_stop == "cancelled":
                # Grok CLI often ends the turn as "cancelled" after a local-tool
                # permission reject. Nudge once in-session so the model finishes
                # with pure reasoning instead of burning API fallback latency.
                state = _TurnState()
                self._turns[session_id] = state
                result = await self._request(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": TOOLLESS_RETRY_PROMPT}],
                    },
                    deadline_seconds=self.timeout_seconds,
                )
        except (TimeoutError, asyncio.CancelledError):
            try:
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": session_id},
                    }
                )
            except RuntimeError:
                pass
            raise
        finally:
            self.in_flight -= 1
            state = self._turns.pop(session_id, state)
        text = state.final_text()
        stop_reason = _normalize_stop_reason(result.get("stopReason"))
        if stop_reason != "end_turn":
            raise RuntimeError(
                f"Grok Build ended without a completed answer (stop reason: {stop_reason})"
            )
        if not text:
            raise RuntimeError("Grok Build completed without a final answer")
        metadata = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
        usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
        self.completed_turns += 1
        return {
            "text": text,
            "model": metadata.get("modelId") or self.model,
            "stop_reason": "EndTurn",
            "session_id": metadata.get("sessionId") or session_id,
            "request_id": metadata.get("requestId"),
            "plane": "grok_build_oauth",
            "billing_class": "subscription_build",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "usage": usage,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "transport": "persistent_acp",
        }

    async def close(self) -> None:
        self._ready = False
        process = self.process
        self.process = None
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        self._fail_pending(RuntimeError("Grok Build ACP runtime closed"))
        if self._runtime is not None:
            self._runtime.cleanup()
            self._runtime = None

    def metrics(self) -> dict[str, Any]:
        return {
            "ready": self._ready
            and self.process is not None
            and self.process.returncode is None,
            "in_flight": self.in_flight,
            "completed_turns": self.completed_turns,
            "uptime_seconds": (
                round(time.monotonic() - self.started_at, 3) if self.started_at is not None else 0
            ),
        }


class GrokBuildACPManager:
    def __init__(self, *, binary: str, auth_path: Path, timeout_seconds: int) -> None:
        self.binary = binary
        self.auth_path = auth_path
        self.timeout_seconds = timeout_seconds
        self._workers: dict[tuple[Any, ...], GrokBuildWorker] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        prompt: str,
        *,
        model: str | None,
        effort: str | None,
        max_turns: int,
        allow_web: bool,
        agentic: bool,
        system_prompt: str,
    ) -> dict[str, Any]:
        resolved_binary = shutil.which(self.binary) or self.binary
        key = (model, effort, max_turns, allow_web, agentic, system_prompt)
        async with self._lock:
            worker = self._workers.get(key)
            if worker is None:
                worker = GrokBuildWorker(
                    binary=resolved_binary,
                    auth_path=self.auth_path,
                    model=model,
                    effort=effort,
                    max_turns=max_turns,
                    allow_web=allow_web,
                    agentic=agentic,
                    system_prompt=system_prompt,
                    timeout_seconds=self.timeout_seconds,
                )
                self._workers[key] = worker
        try:
            return await worker.run(prompt)
        except Exception as exc:
            if (
                not worker._ready
                or worker.process is None
                or worker.process.returncode is not None
            ):
                async with self._lock:
                    if self._workers.get(key) is worker:
                        self._workers.pop(key, None)
            # Same-plane recovery: one forced toolless Build profile avoids
            # cli_cancelled → API fallback (the main benchmark latency killer).
            message = str(exc).lower()
            if agentic and (
                "stop reason: cancelled" in message
                or "without a completed answer" in message
            ):
                recovery = await self.run(
                    f"{TOOLLESS_RETRY_PROMPT}\n\n# Original request\n{prompt}",
                    model=model,
                    effort=effort,
                    max_turns=1,
                    allow_web=False,
                    agentic=False,
                    system_prompt=system_prompt,
                )
                recovery["completion_recovery"] = {
                    "attempted": True,
                    "reason": "cli_cancelled_toolless_profile",
                    "succeeded": True,
                    "attempts": 1,
                }
                return recovery
            raise

    async def close(self) -> None:
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        await asyncio.gather(*(worker.close() for worker in workers), return_exceptions=True)

    def metrics(self) -> dict[str, Any]:
        workers = [worker.metrics() for worker in self._workers.values()]
        return {
            "transport": "persistent_acp",
            "provider_managed_concurrency": True,
            "worker_profiles": len(workers),
            "ready_workers": sum(1 for worker in workers if worker["ready"]),
            "in_flight": sum(int(worker["in_flight"]) for worker in workers),
            "completed_turns": sum(int(worker["completed_turns"]) for worker in workers),
        }
