import os
import json
import base64
import sys
import inspect
import logging
import threading
import time
import hashlib
import re
import uuid
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import sqlite3
import contextlib
import contextvars
import asyncio
import functools
import concurrent.futures
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Literal, Callable
from dotenv import load_dotenv
from pydantic import BaseModel, Field as PydanticField

# Path Resolver for zero-config portability
class PathResolver:
    @staticmethod
    def get_project_root() -> Path:
        # In containers the app code is baked at /app while the live project is
        # bind-mounted elsewhere (compose mounts it at /workspace); WORKSPACE_ROOT
        # points file/git access at the mount. Unset = repo containing this file.
        override = os.environ.get("WORKSPACE_ROOT", "").strip()
        if override:
            return Path(override)
        return Path(__file__).resolve().parents[1]

    @classmethod
    def get_state_base_dir(cls) -> Optional[Path]:
        state_dir = os.environ.get("UNIGROK_STATE_DIR")
        if state_dir:
            base_dir = Path(state_dir).expanduser()
            base_dir.mkdir(parents=True, exist_ok=True)
            return base_dir
        if get_unigrok_runtime() == "cloudrun":
            base_dir = Path(tempfile.gettempdir()) / "uni-grok"
            base_dir.mkdir(parents=True, exist_ok=True)
            return base_dir
        return None

    @classmethod
    def get_logs_dir(cls) -> Path:
        state_base = cls.get_state_base_dir()
        logs_dir = (state_base / "logs") if state_base else (cls.get_project_root() / "logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    @classmethod
    def get_chats_dir(cls) -> Path:
        if os.environ.get("UNI_GROK_TESTING") == "1":
            default_test_dir = str(Path(tempfile.gettempdir()) / "test_chats")
            test_chats_dir = Path(os.environ.get("UNI_GROK_TEST_CHATS_DIR", default_test_dir))
            test_chats_dir.mkdir(exist_ok=True)
            return test_chats_dir
        state_base = cls.get_state_base_dir()
        chats_dir = (state_base / "chats") if state_base else (cls.get_project_root() / "chats")
        chats_dir.mkdir(parents=True, exist_ok=True)
        return chats_dir

    @classmethod
    def get_grok_cli_path(cls) -> str:
        # 1. Search in PATH
        which_path = shutil.which("grok")
        if which_path:
            return which_path

        # 2. Check standard paths
        standard_paths = [
            Path.home() / ".grok/bin/grok",
            Path("/usr/local/bin/grok"),
            Path("/opt/homebrew/bin/grok"),
        ]
        for p in standard_paths:
            if p.exists():
                return str(p)

        # 3. Fallback default
        return str(Path.home() / ".grok/bin/grok")

    @classmethod
    def get_uv_path(cls) -> str:
        # 1. Search in PATH
        which_path = shutil.which("uv")
        if which_path:
            return which_path

        # 2. Check standard paths
        standard_paths = [
            Path.home() / ".local/bin/uv",
            Path("/usr/local/bin/uv"),
            Path("/opt/homebrew/bin/uv"),
        ]
        for p in standard_paths:
            if p.exists():
                return str(p)

        # 3. Fallback default
        return str(Path.home() / ".local/bin/uv")

    @classmethod
    def validate_path(cls, path_str: str) -> Path:
        """Resolve path and ensure it lies within the project root."""
        root = cls.get_project_root().resolve()
        path = Path(path_str)
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise PermissionError(f"Access denied: path '{path_str}' is outside the project root '{root}'")
        return resolved


def grok_cli_available() -> bool:
    """True when a grok CLI binary is resolvable on this host — the gate the
    local CLI plane needs (binary presence only; auth validity is the
    CLI's own concern at call time)."""
    path = PathResolver.get_grok_cli_path()
    return bool(shutil.which(path) or Path(path).exists())


def grok_cli_check_ready(timeout_sec: float = 2.0) -> bool:
    """Run the CLI's own bounded health probe for routing decisions."""
    if not grok_cli_available():
        return False
    try:
        completed = subprocess.run(
            [PathResolver.get_grok_cli_path(), "--check"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1.0, float(timeout_sec)),
            check=False,
        )
    except Exception:
        return False
    return completed.returncode == 0


def get_unigrok_runtime() -> str:
    return os.environ.get("UNIGROK_RUNTIME", "local").strip().lower() or "local"


def is_cloudrun_runtime() -> bool:
    return get_unigrok_runtime() == "cloudrun"


def local_context_enabled() -> bool:
    if is_cloudrun_runtime():
        return os.environ.get("UNIGROK_ENABLE_LOCAL_CONTEXT", "").lower() in ("1", "true", "yes")
    return True

# Initialize configurations
root_dir = PathResolver.get_project_root()
env_path = root_dir / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(root_dir / "example.env")

XAI_API_KEY = os.getenv("XAI_API_KEY", "")

_LOCAL_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None


def _local_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _LOCAL_EXECUTOR
    if _LOCAL_EXECUTOR is None:
        max_workers = max(1, int(os.getenv("UNIGROK_THREAD_WORKERS", "8")))
        _LOCAL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="unigrok-io",
        )
    return _LOCAL_EXECUTOR


# Timed run_blocking calls each get a dedicated daemon thread; this counter
# bounds how many can be in flight at once. A timed-out call strands its
# thread until the callable truly returns, and stranded threads keep counting
# against the cap — sustained pressure here means upstream calls are hanging.
_TIMED_THREADS_LOCK = threading.Lock()
_TIMED_THREADS_IN_FLIGHT = 0
_TIMED_THREADS_PEAK = 0


def _max_timed_threads() -> int:
    try:
        return max(1, int(os.getenv("UNIGROK_MAX_TIMED_THREADS", "64")))
    except ValueError:
        return 64


def get_runtime_stats() -> Dict[str, int]:
    """Snapshot of timed-thread pressure (consumed by grok_mcp_status)."""
    with _TIMED_THREADS_LOCK:
        return {
            "timed_threads_in_flight": _TIMED_THREADS_IN_FLIGHT,
            "timed_threads_peak": _TIMED_THREADS_PEAK,
        }


async def run_blocking(fn: Callable, *args, timeout: Optional[float] = None, **kwargs):
    """Run blocking SDK/local work on a bounded executor.

    Timed calls get a dedicated daemon thread instead of a shared-pool worker:
    a call that outlives its timeout then only strands its own thread, whereas
    with the shared pool eight stuck calls would permanently occupy every
    worker and deadlock all SDK bridging in the server. The dedicated threads
    are capped (UNIGROK_MAX_TIMED_THREADS, default 64): at capacity the call
    fails fast with RuntimeError instead of spawning yet another thread.
    """
    call = functools.partial(fn, *args, **kwargs)
    loop = asyncio.get_running_loop()
    if timeout is not None:
        global _TIMED_THREADS_IN_FLIGHT, _TIMED_THREADS_PEAK
        cap = _max_timed_threads()
        with _TIMED_THREADS_LOCK:
            if _TIMED_THREADS_IN_FLIGHT >= cap:
                raise RuntimeError(
                    f"timed-call capacity exhausted ({_TIMED_THREADS_IN_FLIGHT} in flight)"
                )
            _TIMED_THREADS_IN_FLIGHT += 1
            _TIMED_THREADS_PEAK = max(_TIMED_THREADS_PEAK, _TIMED_THREADS_IN_FLIGHT)
        future = loop.create_future()

        def _resolve(result, exc):
            if future.done():
                return  # Timed out and cancelled — nothing left to deliver.
            if exc is not None:
                future.set_exception(exc)
            else:
                future.set_result(result)

        def _runner():
            global _TIMED_THREADS_IN_FLIGHT
            try:
                try:
                    result = call()
                except BaseException as exc:  # noqa: BLE001 — delivered to the caller
                    result, delivered_exc = None, exc
                else:
                    delivered_exc = None
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(_resolve, result, delivered_exc)
            finally:
                with _TIMED_THREADS_LOCK:
                    _TIMED_THREADS_IN_FLIGHT -= 1

        try:
            threading.Thread(target=_runner, name="unigrok-io-timed", daemon=True).start()
        except BaseException:
            with _TIMED_THREADS_LOCK:
                _TIMED_THREADS_IN_FLIGHT -= 1
            raise
        return await asyncio.wait_for(future, timeout=timeout)
    return await loop.run_in_executor(_local_executor(), call)


async def communicate_with_timeout(proc: Any, timeout_sec: float, input_data: Optional[bytes] = None):
    """Communicate with a subprocess and always reap it on timeout."""
    try:
        communicate = proc.communicate() if input_data is None else proc.communicate(input_data)
        return await asyncio.wait_for(communicate, timeout=timeout_sec)
    except asyncio.TimeoutError:
        with contextlib.suppress(AttributeError, ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except Exception:
            with contextlib.suppress(AttributeError, ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise

# ─── Caller identity (multi-agent workspace) ─────────────────────────────────
# This MCP is shared by several coding agents (Claude/Codex/Gemini — see
# .agents/AGENTS.md). The caller identity is a short free-text name derived
# from the MCP clientInfo sent at initialize (stdio) or from the gateway's
# X-Caller header / auth-key alias (HTTP). It is attributed to telemetry
# rows, session message metadata, and research-job rows, and drives the
# optional per-caller daily budgets. Everything degrades to caller=None.

_ACTIVE_CALLER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_active_caller", default=None
)

_ACTIVE_CLIENT_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_http_client_id", default=None
)

_ACTIVE_SESSION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_http_session_id", default=None
)


def scoped_session(session: Optional[str]) -> Optional[str]:
    """Prefix an explicit session name with the requesting client id so each
    IDE keeps its own history ('vscode:main'). No client id, or no session,
    leaves the name untouched."""
    client_id = _ACTIVE_CLIENT_ID.get()
    if not session:
        session = _ACTIVE_SESSION_ID.get()
    if client_id and session and not session.startswith(f"{client_id}:"):
        return f"{client_id}:{session}"
    return session


def normalize_caller(value: Any) -> Optional[str]:
    """Sanitize a caller identity: strip control characters, trim, and bound
    to 80 chars (it lands in db rows and metrics keys). None/blank -> None."""
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    return text[:80] or None


def set_active_caller(caller: Optional[str]):
    """Bind the caller identity to the current async context (the HTTP
    gateway middleware does this per request). Returns the reset token."""
    return _ACTIVE_CALLER.set(normalize_caller(caller))


def reset_active_caller(token) -> None:
    with contextlib.suppress(Exception):
        _ACTIVE_CALLER.reset(token)


def get_active_caller() -> Optional[str]:
    return _ACTIVE_CALLER.get()


def caller_from_mcp_context(ctx: Any) -> Optional[str]:
    """Caller identity from an injected FastMCP Context.

    Introspected against the installed mcp 1.26: ctx.session (the
    ServerSession) exposes client_params — the InitializeRequestParams the
    client sent — whose clientInfo (mcp.types.Implementation) carries
    name/version. Degrades to None for clients that never completed
    initialize, contexts used outside a request (both raise), or SDK layouts
    without client_params.
    """
    try:
        params = getattr(getattr(ctx, "session", None), "client_params", None)
        info = getattr(params, "clientInfo", None)
        return normalize_caller(getattr(info, "name", None))
    except Exception:
        return None


def telemetry_row_caller(row: Dict[str, Any]) -> Optional[str]:
    """Caller name from a telemetry row's metadata column (raw JSON text from
    the db, or an already-parsed dict from mocks). None for pre-v8 rows,
    unattributed traffic, and malformed metadata."""
    meta = row.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return None
    if not isinstance(meta, dict):
        return None
    return normalize_caller(meta.get("caller"))


class CallerBudgetExceeded(RuntimeError):
    """A caller's UNIGROK_CALLER_BUDGETS daily spend is at/over its limit.

    Raised by orchestrate() BEFORE any model work; FastMCP surfaces it to the
    client as a tool error (isError), never a server crash."""


# Per-budget-entry spend cache: entry substring -> (spent_usd, fetched_at).
# ~60s of staleness is the accepted trade for a zero-read hot path between
# refreshes (also bounds how far past midnight yesterday's total lingers).
_CALLER_SPEND_CACHE: Dict[str, tuple] = {}
_CALLER_SPEND_TTL_SEC = 60.0


def _caller_budgets() -> Dict[str, float]:
    """UNIGROK_CALLER_BUDGETS parsed as a JSON dict {caller_substring:
    daily_usd}. Unset/blank/malformed -> {} (malformed warns, never raises)."""
    raw = os.environ.get("UNIGROK_CALLER_BUDGETS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("must be a JSON object of {caller_substring: daily_usd}")
        budgets: Dict[str, float] = {}
        for key, value in data.items():
            name = str(key).strip()
            if name:
                budgets[name] = float(value)
        return budgets
    except Exception as exc:
        logging.getLogger("GrokMCP").warning(
            f"Ignoring malformed UNIGROK_CALLER_BUDGETS: {exc}"
        )
        return {}


def _match_caller_budget(caller: str, budgets: Dict[str, float]) -> Optional[tuple]:
    """The budget entry governing this caller: case-insensitive substring
    match against the caller name; the LONGEST matching substring wins so a
    specific entry ('claude-code') beats a broad one ('claude')."""
    lowered = caller.lower()
    matches = [(key, limit) for key, limit in budgets.items() if key.lower() in lowered]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))


async def enforce_caller_budget(store_param: Any, caller: Optional[str]) -> None:
    """Pre-execution per-caller daily budget gate.

    No-op unless UNIGROK_CALLER_BUDGETS is set AND the caller matches an
    entry (unset env returns before any parsing — zero hot-path cost by
    default). Spend is today's telemetry cost across every caller matching
    the entry's substring (the entry IS the shared pot), read via one
    created_at-indexed query and cached ~60s per entry. At/over budget raises
    CallerBudgetExceeded; a failing store read degrades OPEN — a broken
    telemetry table must not block traffic.
    """
    if not caller or not os.environ.get("UNIGROK_CALLER_BUDGETS", "").strip():
        return
    match = _match_caller_budget(caller, _caller_budgets())
    if match is None:
        return
    entry, limit_usd = match
    active_store = store_param if store_param is not None else store
    if active_store is None:
        return
    now = time.time()
    cached = _CALLER_SPEND_CACHE.get(entry)
    if cached is not None and (now - cached[1]) < _CALLER_SPEND_TTL_SEC:
        spent = cached[0]
    else:
        try:
            spent = float(await active_store.get_caller_cost_today(entry))
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(
                f"Caller budget check unavailable (degrading open): {exc}"
            )
            return
        _CALLER_SPEND_CACHE[entry] = (spent, now)
    if spent >= limit_usd:
        raise CallerBudgetExceeded(
            f"daily budget exhausted for {caller}: ${spent:.2f}/${limit_usd:.2f}"
        )


# ─── Request correlation ids (observability) ────────────────────────────────
# Every agent call carries a short correlation id bound to the current async
# context: the HTTP gateway derives it from an incoming W3C traceparent (or
# generates one) and echoes it as X-Request-Id; stdio calls generate one at
# orchestrate/run_agent_turn entry. The id rides MetaLayer.request_id,
# telemetry metadata, job rows, and every log line (RequestContextLogFilter).
# Everything degrades to "" when nothing is bound.

_ACTIVE_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "unigrok_request_id", default=""
)


def new_request_id() -> str:
    """Fresh short correlation id: the first 12 hex chars of a uuid4 — unique
    enough to grep logs and cheap enough to stamp on every row."""
    return uuid.uuid4().hex[:12]


def normalize_request_id(value: Any) -> str:
    """Sanitize a request id for logs/db rows/headers: keep only URL- and
    header-safe chars, bound to 64 (a W3C trace-id is 32 hex). Blank -> ""."""
    return re.sub(r"[^0-9A-Za-z._\-]", "", str(value or ""))[:64]


def set_request_id(value: Any):
    """Bind a request id to the current async context (the HTTP gateway
    middleware does this per request). Returns the reset token."""
    return _ACTIVE_REQUEST_ID.set(normalize_request_id(value))


def reset_request_id(token) -> None:
    with contextlib.suppress(Exception):
        _ACTIVE_REQUEST_ID.reset(token)


def get_request_id() -> str:
    return _ACTIVE_REQUEST_ID.get()


@contextlib.contextmanager
def request_id_scope():
    """Guarantee a bound request id for the duration of one agent call.

    Respects an inherited id (gateway traceparent, an outer agent call);
    otherwise generates a fresh one and RESETS it on exit so two sequential
    calls in the same task never share a correlation id."""
    existing = _ACTIVE_REQUEST_ID.get()
    if existing:
        yield existing
        return
    token = _ACTIVE_REQUEST_ID.set(new_request_id())
    try:
        yield _ACTIVE_REQUEST_ID.get()
    finally:
        reset_request_id(token)


def _with_request_id(fn):
    """Decorator for the agent entrypoints (orchestrate/run_agent_turn):
    runs the call inside request_id_scope() and stamps the id onto the
    returned MetaLayer."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with request_id_scope() as request_id:
            result = await fn(*args, **kwargs)
            if isinstance(result, MetaLayer) and not result.request_id:
                result.request_id = request_id
            return result

    return wrapper


FALLBACK_XAI_LANGUAGE_MODELS = [
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-0309-reasoning",
    "grok-4.20-multi-agent",
    "grok-build-0.1",
    "grok-2-1212",
    "grok-beta",
    "grok-vision-beta",
    "grok-3",
]

FALLBACK_GROK_CLI_MODELS = [
    "grok-build",
    "grok-composer-2.5-fast",
]
CLI_MODEL_IDS = tuple(FALLBACK_GROK_CLI_MODELS)


def xai_api_key_configured() -> bool:
    return bool((os.environ.get("XAI_API_KEY") or XAI_API_KEY or "").strip())


def cli_plane_ready_for_local_runtime() -> bool:
    if is_cloudrun_runtime() or not grok_cli_available():
        return False
    # Unit tests patch grok_cli_available() to model routing without launching
    # a real local CLI. Production routing uses `grok --check` so a present but
    # unauthenticated CLI does not look like a usable plane.
    if os.environ.get("UNI_GROK_TESTING") == "1":
        return True
    return grok_cli_check_ready()


def _normalize_cli_max_turns(max_turns: Optional[int]) -> Optional[str]:
    if max_turns is None:
        return None
    try:
        turns = int(max_turns)
    except (TypeError, ValueError):
        return None
    return str(turns) if turns > 0 else None


def _json_schema_for_cli(schema: Any) -> Optional[str]:
    """Serialize structured-output schema input for `grok --json-schema`."""
    if schema is None:
        return None
    if isinstance(schema, str):
        stripped = schema.strip()
        return stripped or None
    if inspect.isclass(schema) and issubclass(schema, BaseModel):
        schema = schema.model_json_schema()
    elif isinstance(schema, BaseModel):
        schema = schema.__class__.model_json_schema()
    elif hasattr(schema, "model_json_schema") and callable(schema.model_json_schema):
        schema = schema.model_json_schema()
    return json.dumps(schema, sort_keys=True, separators=(",", ":"))


def _build_grok_cli_args(
    cli_prompt: str,
    model_name: str,
    dynamic_sys_prompt: str,
    output_format: str,
    cli_session_id: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    max_turns: Optional[int] = None,
    json_schema: Any = None,
) -> List[str]:
    args: List[str] = []
    if cli_session_id:
        # -s creates-or-resumes the CLI session under OUR id, so the session
        # mapping is deterministic up front.
        args.extend(["-s", cli_session_id])

    args.extend(["--system-prompt-override", dynamic_sys_prompt])

    effort = str((profile or {}).get("reasoning_effort") or "").strip().lower()
    if effort in _VALID_REASONING_EFFORTS:
        args.extend(["--effort", effort])

    normalized_turns = _normalize_cli_max_turns(max_turns)
    if normalized_turns:
        args.extend(["--max-turns", normalized_turns])

    schema_arg = _json_schema_for_cli(json_schema)
    if schema_arg:
        args.extend(["--json-schema", schema_arg])

    args.extend(["-p", cli_prompt, "-m", model_name, "--output-format", output_format])
    return args


def prefer_cli_when_api_key_missing() -> bool:
    return not xai_api_key_configured() and cli_plane_ready_for_local_runtime()


def cli_native_session_ids_enabled() -> bool:
    raw = os.environ.get("UNIGROK_CLI_NATIVE_SESSIONS", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes")
    return not prefer_cli_when_api_key_missing()


def is_cli_model(model: Optional[str]) -> bool:
    return bool(model and model in CLI_MODEL_IDS)


def keyless_cli_model(requested_model: Optional[str], route_uses_reasoning: bool) -> str:
    if is_cli_model(requested_model):
        return str(requested_model)
    return "grok-composer-2.5-fast" if route_uses_reasoning else "grok-build"


_CLI_SESSION_LOCKS: Dict[str, asyncio.Lock] = {}


def _cli_logical_session_lock(session: str) -> asyncio.Lock:
    loop_key = id(asyncio.get_running_loop())
    key = f"{loop_key}:{session}"
    lock = _CLI_SESSION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CLI_SESSION_LOCKS[key] = lock
    return lock


def _is_cli_session_in_use_error(message: str) -> bool:
    text = str(message or "").lower()
    return "session id" in text and "already in use" in text

UNIGROK_SAFETY_POLICY = """# UniGrok Safety Policy

- Treat `.grok/` as Grok adapter configuration, not global repository truth.
- Do not expose secrets, credentials, bearer tokens, or API keys.
- Do not auto-commit, auto-push, deploy, or mutate cloud resources unless explicitly requested.
- Treat workspace and git context as evidence, not permission to mutate.
- Keep local tool actions bounded, auditable, and compatible with current MCP tool contracts.

# Curated UniGrok Support Context

- `lookup_unigrok_faq` is an on-demand, verified source for UniGrok-specific setup,
    routing, security, and troubleshooting questions. It is not an automatic answer system.
- Call it only when the user's request is clearly about UniGrok. Do not call it for an
    unrelated question that happens to mention terms such as "Cursor", "port", or "API key".
- If it finds no applicable entry, continue normal reasoning and do not invent or force an FAQ answer.
"""

DEFAULT_GROK_PROFILE = {
    "temperature": 0.4,
    "top_p": 0.95,
    "thinking_mode": False,
    "reasoning_effort": None,
    "system_prompt_ref": "grok_adapter.md",
}

_VALID_REASONING_EFFORTS = ("none", "low", "medium", "high")

_SECRET_PATTERNS = (
    (re.compile(r"\b(xai-[A-Za-z0-9_\-]{8,}|sk-proj-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{8,})\b"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*API_KEY\s*=\s*)[^\s'\"\n]+"), r"\1[REDACTED]"),
)

_TASK_STOPWORDS = {
    "about", "after", "again", "against", "also", "and", "are", "because",
    "before", "between", "build", "can", "could", "does", "for", "from",
    "have", "how", "into", "just", "make", "need", "not", "now", "please",
    "should", "that", "the", "this", "through", "using", "what", "when",
    "where", "with", "would", "you", "your",
}


def _grok_dir() -> Path:
    return PathResolver.get_project_root() / ".grok"


def _grok_hyperparams_dir() -> Path:
    return _grok_dir() / "hyperparams"


def _grok_prompts_dir() -> Path:
    return _grok_dir() / "prompts"


def _safe_basename(value: str) -> Optional[str]:
    name = os.path.basename(str(value or "").strip())
    if not name or name != str(value or "").strip():
        return None
    return name


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, parsed))


def _profile_file_exists(profile_name: str) -> bool:
    safe_name = _safe_basename(profile_name)
    if not safe_name:
        return False
    return (_grok_hyperparams_dir() / f"{Path(safe_name).stem}.json").exists()


def _default_profile_name(profile_or_model: str) -> str:
    value = str(profile_or_model or "").strip()
    if _profile_file_exists(value):
        return Path(value).stem
    lowered = value.lower()
    if any(hint in lowered for hint in ("code", "build", "composer")) and _profile_file_exists("grok-code-fast-1"):
        return "grok-code-fast-1"
    if any(hint in lowered for hint in ("reason", "grok-4", "4.3", "4.20")) and _profile_file_exists("grok-4-1-fast-reasoning"):
        return "grok-4-1-fast-reasoning"
    if _profile_file_exists("grok-4-1-fast-non-reasoning"):
        return "grok-4-1-fast-non-reasoning"
    if _profile_file_exists("grok-code-fast-1"):
        return "grok-code-fast-1"
    return Path(value).stem if value else "default"


def _normalize_prompt_ref(prompt_ref: str) -> Optional[str]:
    name = _safe_basename(prompt_ref)
    if not name or not name.endswith(".md"):
        return None
    return name


def load_grok_profile(profile_or_model: str) -> Dict[str, Any]:
    """Load a bounded Grok model profile from `.grok/hyperparams`."""
    profile_name = _default_profile_name(profile_or_model)
    profile = dict(DEFAULT_GROK_PROFILE)
    profile.update({"profile": profile_name, "source": "default"})

    path = _grok_hyperparams_dir() / f"{profile_name}.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                profile.update(raw)
                profile["source"] = str(path)
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(f"Failed to load Grok profile {path}: {exc}")

    profile["profile"] = profile_name
    profile["temperature"] = _clamp_float(
        profile.get("temperature"),
        0.0,
        2.0,
        float(DEFAULT_GROK_PROFILE["temperature"]),
    )
    profile["top_p"] = _clamp_float(
        profile.get("top_p"),
        0.0,
        1.0,
        float(DEFAULT_GROK_PROFILE["top_p"]),
    )
    profile["thinking_mode"] = bool(profile.get("thinking_mode"))
    effort = str(profile.get("reasoning_effort") or "").strip().lower()
    profile["reasoning_effort"] = effort if effort in _VALID_REASONING_EFFORTS else None
    prompt_ref = _normalize_prompt_ref(str(profile.get("system_prompt_ref") or ""))
    profile["system_prompt_ref"] = prompt_ref or DEFAULT_GROK_PROFILE["system_prompt_ref"]
    return profile


def load_grok_prompt(prompt_ref: str) -> str:
    """Read a Grok adapter prompt from `.grok/prompts` with traversal protection."""
    safe_ref = _normalize_prompt_ref(prompt_ref)
    if not safe_ref:
        logging.getLogger("GrokMCP").warning(f"Rejected unsafe Grok prompt ref: {prompt_ref}")
        return ""
    path = _grok_prompts_dir() / safe_ref
    try:
        resolved = path.resolve()
        resolved.relative_to(_grok_prompts_dir().resolve())
        return resolved.read_text(encoding="utf-8")
    except Exception as exc:
        logging.getLogger("GrokMCP").warning(f"Failed to load Grok prompt {safe_ref}: {exc}")
        return ""


def redact_secrets(text: str) -> str:
    redacted = str(text or "")
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _task_terms(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9_]{3,}", str(text or "").lower())
    terms = sorted({token for token in tokens if token not in _TASK_STOPWORDS})
    return terms[:64]


def _task_hash(text: str) -> str:
    terms = _task_terms(text)
    basis = " ".join(terms) if terms else str(text or "").strip().lower()
    return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _bounded_redacted(text: str, limit: int) -> str:
    value = redact_secrets(text).strip()
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[...truncated {len(value) - limit} chars]"


def _normalize_fact_scope(scope: Any) -> str:
    """Knowledge scope normalized for storage AND lookup: 'global' or a short
    session name. Client-controlled via remember_fact, so it gets the same
    redact-and-bound treatment as its sibling columns — hard-sliced (no
    truncation marker) because scope is an exact-match key, and save/search
    must normalize identically to keep matching."""
    value = redact_secrets(str(scope or "global")).strip()[:200]
    return value or "global"


def _tool_output_limit() -> int:
    try:
        return max(1000, int(os.getenv("UNIGROK_TOOL_OUTPUT_MAX_CHARS", "8000")))
    except ValueError:
        return 8000


def _env_timeout(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer limit from the environment without allowing extremes."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def input_limit(name: str, default: int, minimum: int, maximum: int) -> int:
    """Named resource-limit helper shared by local and media tools."""
    return bounded_env_int(name, default, minimum, maximum)


def validate_local_input(
    path: Path,
    *,
    max_bytes: int,
    allowed_suffixes: Optional[tuple[str, ...]] = None,
    label: str = "file",
) -> Path:
    """Validate a resolved local input before any unbounded read occurs."""
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    if allowed_suffixes and path.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(allowed_suffixes)
        raise ValueError(f"Unsupported {label} type '{path.suffix or 'none'}'; allowed: {allowed}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes} byte limit ({size} bytes)")
    return path


def bound_tool_output(text: str, limit: Optional[int] = None) -> str:
    value = redact_secrets(str(text or ""))
    max_chars = _tool_output_limit() if limit is None else max(1000, int(limit))
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"\n[...truncated {len(value) - max_chars} chars from tool output]"


def format_task_memory_notes(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return ""
    lines = ["# Prior UniGrok Task Memory"]
    for item in memories[:3]:
        status = "success" if int(item.get("success") or 0) else "failure"
        profile = item.get("profile") or "unknown"
        plane = item.get("plane") or "unknown"
        summary = str(item.get("outcome_summary") or "").replace("\n", " ")[:500]
        meta = item.get("metadata")
        escalated_note = (
            " (needed escalation to the planning model)"
            if isinstance(meta, dict) and meta.get("escalated")
            else ""
        )
        lines.append(f"- {status} via {plane}/{profile}{escalated_note}: {summary}")
    return "\n".join(lines)


def _split_caller_instructions(system_prompt_text: str) -> tuple[str, str]:
    marker = "\nAdditional Instructions:\n"
    if marker not in system_prompt_text:
        return system_prompt_text, ""
    workspace_part, caller_part = system_prompt_text.split(marker, 1)
    return workspace_part, caller_part


def compose_system_prompt(
    workspace_context: str,
    adapter_prompt: str = "",
    memory_notes: str = "",
    caller_instructions: str = "",
) -> str:
    parts = [
        UNIGROK_SAFETY_POLICY.strip(),
        str(workspace_context or "").strip(),
        str(memory_notes or "").strip(),
        str(adapter_prompt or "").strip(),
        ("# Caller Instructions\n" + caller_instructions.strip()) if caller_instructions.strip() else "",
    ]
    return "\n\n".join(part for part in parts if part)


def current_policy_mode() -> str:
    if is_cloudrun_runtime():
        return "cloudrun-remote-only"
    if get_unigrok_runtime() == "local" and os.environ.get("ENABLE_GIT_WRITE") == "1":
        return "local-write-enabled"
    return "local-readonly"


def inspect_grok_adapter() -> Dict[str, Any]:
    profile_warnings = []
    profiles = []
    prompts = []
    hyperparams_dir = _grok_hyperparams_dir()
    prompts_dir = _grok_prompts_dir()
    if hyperparams_dir.exists():
        for path in sorted(hyperparams_dir.glob("*.json")):
            profiles.append(path.name)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    profile_warnings.append(f"{path.name}: expected JSON object")
                    continue
                for required in ("temperature", "top_p", "system_prompt_ref"):
                    if required not in data:
                        profile_warnings.append(f"{path.name}: missing {required}")
            except Exception as exc:
                profile_warnings.append(f"{path.name}: {exc}")
    if prompts_dir.exists():
        prompts = [path.name for path in sorted(prompts_dir.glob("*.md"))]
    return {
        "profile_count": len(profiles),
        "profiles": profiles,
        "prompts": prompts,
        "warnings": profile_warnings,
    }


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(text or ""))


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _parse_grok_cli_models_output(output: str) -> Dict[str, Any]:
    """Parse `grok models` text output into model ids and warnings."""
    cleaned = _strip_ansi(output)
    models: List[str] = []
    warnings: List[str] = []
    default_model = None
    in_models = False

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if "warn" in lower or "error" in lower or "failed" in lower:
            warnings.append(line)
        if lower.startswith("default model:"):
            default_model = line.split(":", 1)[1].strip() or None
            continue
        if lower.startswith("available models"):
            in_models = True
            continue
        if not in_models:
            continue

        candidate = re.sub(r"^[*\-\u2022\s]+", "", line)
        candidate = re.sub(r"\s+\(default\)\s*$", "", candidate).strip()
        match = re.match(r"(grok[A-Za-z0-9._/\-]+)", candidate)
        if match:
            models.append(match.group(1))

    if default_model and default_model not in models:
        models.insert(0, default_model)

    return {
        "models": _dedupe_preserve_order(models),
        "default_model": default_model,
        "warnings": _dedupe_preserve_order(warnings),
    }


async def discover_xai_api_models() -> Dict[str, Any]:
    """Discover xAI API language models, falling back to known API model ids."""
    def _list_models():
        client = get_xai_client()
        return client.models.list_language_models()

    try:
        models = await run_blocking(_list_models, timeout=10.0)
        entries = []
        for model in models:
            name = getattr(model, "name", str(model))
            if not name:
                continue
            entry = {"id": name}
            max_prompt_length = getattr(model, "max_prompt_length", None)
            if max_prompt_length:
                entry["context_window"] = max_prompt_length
            entries.append(entry)
        # Opportunistic default-slug validation: warn loudly when a routing
        # default is missing from the live catalog (retired/renamed model).
        live_ids = {entry["id"] for entry in entries}
        for default_model in (DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL):
            if default_model not in live_ids:
                logging.getLogger("GrokMCP").warning(
                    f"Default model '{default_model}' is absent from the live xAI "
                    "model catalog; routing may target a retired slug."
                )
        return {
            "models": sorted(entries, key=lambda item: item["id"]),
            "available": True,
            "warnings": [],
            "source": "xai_api",
        }
    except Exception as exc:
        return {
            "models": [{"id": model_id} for model_id in FALLBACK_XAI_LANGUAGE_MODELS],
            "available": False,
            "warnings": [f"xAI API model discovery failed: {exc}"],
            "source": "xai_api_fallback",
        }


async def discover_grok_cli_models(timeout_sec: float = 5.0) -> Dict[str, Any]:
    """Discover local Grok CLI models with Cloud Run and failure safeguards."""
    if is_cloudrun_runtime():
        return {
            "models": [],
            "default_model": None,
            "available": False,
            "warnings": ["Grok CLI unavailable in Cloud Run runtime."],
            "source": "cloudrun-disabled",
        }

    grok_path = PathResolver.get_grok_cli_path()
    try:
        proc = await asyncio.create_subprocess_exec(
            grok_path,
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await communicate_with_timeout(proc, timeout_sec)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return {
                "models": [{"id": model_id} for model_id in FALLBACK_GROK_CLI_MODELS],
                "default_model": FALLBACK_GROK_CLI_MODELS[0],
                "available": False,
                "warnings": [f"Grok CLI model discovery timed out after {timeout_sec:.1f}s."],
                "source": "cli-fallback",
            }

        parsed = _parse_grok_cli_models_output(
            stdout.decode("utf-8", errors="replace")
            + "\n"
            + stderr.decode("utf-8", errors="replace")
        )
        model_ids = parsed["models"] or FALLBACK_GROK_CLI_MODELS
        warnings = list(parsed["warnings"])
        if proc.returncode != 0:
            warnings.append(f"`grok models` exited with code {proc.returncode}.")
        if not parsed["models"]:
            warnings.append("Grok CLI model discovery returned no parseable models; using fallback CLI model ids.")

        default_model = parsed["default_model"] or model_ids[0]
        return {
            "models": [{"id": model_id, "default": model_id == default_model} for model_id in model_ids],
            "default_model": default_model,
            "available": proc.returncode == 0 and bool(parsed["models"]),
            "warnings": _dedupe_preserve_order(warnings),
            "source": "grok_cli",
        }
    except Exception as exc:
        return {
            "models": [{"id": model_id, "default": index == 0} for index, model_id in enumerate(FALLBACK_GROK_CLI_MODELS)],
            "default_model": FALLBACK_GROK_CLI_MODELS[0],
            "available": False,
            "warnings": [f"Grok CLI model discovery failed: {exc}"],
            "source": "cli-fallback",
        }


def discover_local_grok_profiles() -> Dict[str, Any]:
    """List local `.grok` profiles without treating them as provider models."""
    adapter = inspect_grok_adapter()
    profiles = []
    for filename in adapter["profiles"]:
        profile_name = Path(filename).stem
        profile = load_grok_profile(profile_name)
        profiles.append(
            {
                "name": profile_name,
                "temperature": profile.get("temperature"),
                "top_p": profile.get("top_p"),
                "thinking_mode": profile.get("thinking_mode"),
                "system_prompt_ref": profile.get("system_prompt_ref"),
            }
        )
    return {
        "profiles": profiles,
        "warnings": adapter["warnings"],
        "source": ".grok/hyperparams",
    }


async def build_model_catalog(include_cli: bool = True) -> Dict[str, Any]:
    """Build a structured catalog for API models, local CLI models, and profiles."""
    xai_api = await discover_xai_api_models()
    grok_cli = await discover_grok_cli_models() if include_cli else {
        "models": [],
        "default_model": None,
        "available": False,
        "warnings": ["Grok CLI discovery skipped."],
        "source": "skipped",
    }
    local_profiles = discover_local_grok_profiles()
    warnings = (
        list(xai_api.get("warnings", []))
        + list(grok_cli.get("warnings", []))
        + list(local_profiles.get("warnings", []))
    )
    return {
        "xai_api": xai_api["models"],
        "grok_cli": grok_cli["models"],
        "local_profiles": local_profiles["profiles"],
        "default_cli_model": grok_cli.get("default_model"),
        "warnings": _dedupe_preserve_order(warnings),
        "sources": {
            "xai_api": xai_api.get("source"),
            "grok_cli": grok_cli.get("source"),
            "local_profiles": local_profiles.get("source"),
        },
    }

# Global Client Connection Pool
_client = None
# get_xai_client is called from executor threads — guard the check-then-set so
# concurrent first calls cannot leak duplicate Client instances.
_client_lock = threading.Lock()

def get_xai_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not XAI_API_KEY:
                    raise ValueError("XAI_API_KEY is not configured in the environment.")
                from xai_sdk import Client
                # Collections (knowledge + task-memory mirrors) require a
                # SEPARATE management API key on the xAI side — the inference
                # key alone yields "Please provide a management API key."
                # Optional: unset keeps inference-only behavior, and the
                # mirrors fail open with that exact reason in `rag status`.
                management_key = os.getenv("XAI_MANAGEMENT_API_KEY", "").strip() or None
                _client = Client(api_key=XAI_API_KEY, management_api_key=management_key)
    if _eval_record_enabled():
        # Opt-in eval recording tap (UNIGROK_EVAL_RECORD=1): a thin per-call
        # proxy that appends completed responses to a cassette event log.
        # OFF by default — this branch never runs without the env flag.
        return _EvalRecordingClient(_client)
    return _client

def close_xai_client():
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


# ─── Eval Recording Tap (UNIGROK_EVAL_RECORD) ────────────────────────────────
# OFF by default; zero behavior change while off. With UNIGROK_EVAL_RECORD
# truthy, get_xai_client() hands back a thin proxy whose chats append one
# JSON line per completed sample()/parse() — (model, prompt-hash, response
# content/usage/cost) — to UNIGROK_EVAL_RECORD_FILE (default
# evals/cassettes/recorded.jsonl). Real traffic becomes cassette raw material
# for the evals harness; recording failures never break the underlying call.

_EVAL_RECORD_LOCK = threading.Lock()


def _eval_record_enabled() -> bool:
    return os.environ.get("UNIGROK_EVAL_RECORD", "").strip().lower() in ("1", "true", "yes")


def _eval_record_path() -> Path:
    override = os.environ.get("UNIGROK_EVAL_RECORD_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return PathResolver.get_project_root() / "evals" / "cassettes" / "recorded.jsonl"


def _eval_record_write(event: Dict[str, Any]):
    """Append one event line; sample() runs on executor threads, so writes
    serialize through a process-wide lock. Never raises."""
    try:
        path = _eval_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        with _EVAL_RECORD_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:
        logging.getLogger("GrokMCP").debug(f"Eval record write failed: {exc}")


class _EvalRecordingChat:
    """Chat proxy: delegates everything, records completed responses.

    Only append/sample are intercepted directly; parse is wrapped through
    __getattr__ so hasattr(chat, "parse") keeps EXACT parity with the
    underlying SDK chat (the reflection reviewer capability-gates on it)."""

    def __init__(self, chat: Any, model: str):
        self._chat = chat
        self._model = str(model or "")
        self._prompt_hasher = hashlib.sha256()

    def append(self, message):
        try:
            self._prompt_hasher.update(str(message).encode("utf-8", errors="ignore"))
        except Exception:
            pass
        return self._chat.append(message)

    def _record(self, kind: str, response: Any):
        usage = getattr(response, "usage", None)
        _eval_record_write({
            "ts": datetime.now().isoformat(),
            "kind": kind,
            "model": self._model,
            "prompt_sha256": self._prompt_hasher.hexdigest(),
            # Redacted at rest like every other persisted free-text surface:
            # recorded.jsonl is cassette raw material intended for check-in,
            # and a model answer can echo credentials from injected context.
            "content": _bounded_redacted(
                str(getattr(response, "content", "") or ""), 4000
            ),
            "usage": {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            },
            "cost_usd": float(getattr(response, "cost_usd", 0.0) or 0.0),
        })

    def sample(self):
        response = self._chat.sample()
        self._record("sample", response)
        return response

    def __getattr__(self, name):
        attr = getattr(self._chat, name)
        if name == "parse" and callable(attr):
            def _recorded_parse(*args, **kwargs):
                result = attr(*args, **kwargs)
                try:
                    self._record("parse", result[0] if isinstance(result, tuple) else result)
                except Exception:
                    pass
                return result
            return _recorded_parse
        return attr


class _EvalRecordingChatService:
    def __init__(self, service: Any):
        self._service = service

    def create(self, *args, **kwargs):
        chat = self._service.create(*args, **kwargs)
        model = kwargs.get("model") or (args[0] if args else "")
        return _EvalRecordingChat(chat, model)

    def __getattr__(self, name):
        return getattr(self._service, name)


class _EvalRecordingClient:
    """Client proxy: intercepts chat.create only; every other service
    (models, batch, ...) passes straight through."""

    def __init__(self, client: Any):
        self._client = client

    @property
    def chat(self):
        return _EvalRecordingChatService(self._client.chat)

    def __getattr__(self, name):
        return getattr(self._client, name)


@functools.lru_cache(maxsize=None)
def _chat_create_supports(param: str) -> bool:
    """Capability gate: does the installed xai_sdk's chat.create accept `param`?

    Newer request-surface parameters (reasoning_effort, conversation_id) are
    only forwarded when the installed SDK actually exposes them, mirroring the
    defensive pattern of _build_agentic_tools_schema.
    """
    try:
        from xai_sdk.sync.chat import Client as _SyncChatClient
        return param in inspect.signature(_SyncChatClient.create).parameters
    except Exception:
        return False


def _server_state_enabled() -> bool:
    """UNIGROK_SERVER_STATE=0 kill-switch: disables server-side conversation
    state (store_messages/previous_response_id) and restores full local
    history replay on every turn."""
    return os.environ.get("UNIGROK_SERVER_STATE", "1").strip().lower() not in ("0", "false", "no")


def _server_state_supported() -> bool:
    """Server-side conversation state needs BOTH halves of the SDK surface:
    storing this turn's messages and continuing from a stored response id."""
    return _chat_create_supports("store_messages") and _chat_create_supports("previous_response_id")


# ─── xAI Error Classification + Per-Model Circuit Breaker ────────────────────
# The xAI SDK is gRPC-based: call failures surface as grpc.RpcError with a
# .code() → grpc.StatusCode. Classify by that when present, by HTTP-style
# status attributes or type/message otherwise, defaulting unknown errors to
# retryable so transient upstream weirdness never turns into a hard failure.

class CircuitBreakerOpenError(RuntimeError):
    """Raised to fail fast when a model's circuit breaker is open."""


_FATAL_GRPC_CODES = {
    "INVALID_ARGUMENT",
    "UNAUTHENTICATED",
    "PERMISSION_DENIED",
    "NOT_FOUND",
    "UNIMPLEMENTED",
    "FAILED_PRECONDITION",
    "OUT_OF_RANGE",
}

_FATAL_ERROR_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "unauthenticated",
    "permission denied",
    "forbidden",
    "invalid argument",
    "invalid request",
    "http 400",
    "http 401",
    "http 403",
    "http 404",
    "status 400",
    "status 401",
    "status 403",
    "status 404",
)


def classify_xai_error(exc: Exception) -> str:
    """Classify an xAI call failure as "retryable" (429/5xx/connection/timeout/
    transient) or "fatal" (400/401/403/404, validation). Fatal errors must not
    burn retries — retrying an auth failure only delays the real error."""
    if isinstance(exc, CircuitBreakerOpenError):
        return "fatal"
    # 1. gRPC status code (grpc.RpcError exposes .code() → grpc.StatusCode)
    code_getter = getattr(exc, "code", None)
    if callable(code_getter):
        try:
            code_name = str(getattr(code_getter(), "name", "") or "").upper()
            if code_name:
                return "fatal" if code_name in _FATAL_GRPC_CODES else "retryable"
        except Exception:
            pass
    # 2. HTTP-style numeric status attribute
    for attr in ("status_code", "http_status", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            if value == 429 or value >= 500:
                return "retryable"
            if 400 <= value < 500:
                return "fatal"
    # 3. Type-based: transport/timeout errors are transient; validation is not.
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "retryable"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "fatal"
    # 4. Message-based last resort, defaulting unknown errors to retryable.
    message = str(exc).lower()
    if any(marker in message for marker in _FATAL_ERROR_MARKERS):
        return "fatal"
    return "retryable"


def _retry_after_hint(exc: Exception) -> Optional[float]:
    """Extract a Retry-After delay hint (seconds) when the exception exposes
    one — either as a direct attribute or via HTTP response headers."""
    candidates = [getattr(exc, "retry_after", None)]
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        with contextlib.suppress(Exception):
            candidates.append(headers.get("Retry-After") or headers.get("retry-after"))
    for value in candidates:
        try:
            delay = float(value)
        except (TypeError, ValueError):
            continue
        if delay > 0:
            return min(delay, 60.0)
    return None


def _breaker_threshold() -> int:
    try:
        return max(1, int(os.getenv("UNIGROK_BREAKER_THRESHOLD", "5")))
    except ValueError:
        return 5


def _breaker_cooldown_sec() -> float:
    return _env_timeout("UNIGROK_BREAKER_COOLDOWN_SEC", 60.0)


# model → {"consecutive_failures": int, "opened_at": float|None, "trips": int}
_BREAKER_STATE: Dict[str, Dict[str, Any]] = {}
_BREAKER_LOCK = threading.Lock()


def check_circuit_breaker(model: str):
    """Fail fast with CircuitBreakerOpenError while a model's breaker is open.

    After the cool-down elapses the breaker half-opens: the next call is
    allowed through as a probe; its success closes the breaker, its failure
    re-opens it via record_xai_failure.
    """
    with _BREAKER_LOCK:
        state = _BREAKER_STATE.get(model)
        if not state or not state.get("opened_at"):
            return
        elapsed = time.time() - state["opened_at"]
        cooldown = _breaker_cooldown_sec()
        if elapsed >= cooldown:
            state["opened_at"] = None  # Half-open: allow a probe call.
            return
        raise CircuitBreakerOpenError(
            f"Circuit breaker open for model '{model}' after "
            f"{state['consecutive_failures']} consecutive xAI failures; "
            f"retry in {cooldown - elapsed:.0f}s."
        )


def record_xai_failure(model: str):
    """Count a failed xAI call; open the breaker at the consecutive threshold."""
    threshold = _breaker_threshold()
    with _BREAKER_LOCK:
        state = _BREAKER_STATE.setdefault(
            model, {"consecutive_failures": 0, "opened_at": None, "trips": 0}
        )
        state["consecutive_failures"] += 1
        if state["consecutive_failures"] >= threshold and not state.get("opened_at"):
            state["opened_at"] = time.time()
            state["trips"] += 1
            logging.getLogger("GrokMCP").warning(
                f"Circuit breaker OPENED for model '{model}' after "
                f"{state['consecutive_failures']} consecutive failures; "
                f"cooling down for {_breaker_cooldown_sec():.0f}s."
            )


def record_xai_success(model: str):
    """Reset a model's breaker after any successful xAI call."""
    with _BREAKER_LOCK:
        state = _BREAKER_STATE.get(model)
        if state:
            state["consecutive_failures"] = 0
            state["opened_at"] = None


def get_circuit_breaker_state() -> Dict[str, Any]:
    """Snapshot of per-model breaker state (consumed by grok_mcp_status)."""
    now = time.time()
    cooldown = _breaker_cooldown_sec()
    with _BREAKER_LOCK:
        snapshot = {}
        for model, state in _BREAKER_STATE.items():
            opened_at = state.get("opened_at")
            remaining = max(0.0, cooldown - (now - opened_at)) if opened_at else 0.0
            snapshot[model] = {
                "open": bool(opened_at and remaining > 0),
                "consecutive_failures": int(state.get("consecutive_failures", 0)),
                "cooldown_remaining_sec": round(remaining, 1),
                "trips": int(state.get("trips", 0)),
            }
        return snapshot

class RequestContextLogFilter(logging.Filter):
    """Injects the bound request id and caller into every record that passes
    through a handler carrying this filter.

    record.request_id / record.caller hold the raw values ("" when unset) for
    the JSON formatter; record.rid_suffix is a pre-formatted " [rid=<id>]"
    fragment so the plain format stays byte-identical to the historical
    format when no request id is bound. Never raises — logging must survive
    interpreter shutdown and foreign threads (where the contextvars simply
    read as unset).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rid = get_request_id()
        except Exception:
            rid = ""
        try:
            caller = get_active_caller() or ""
        except Exception:
            caller = ""
        record.request_id = rid
        record.caller = caller
        record.rid_suffix = f" [rid={rid}]" if rid else ""
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, stdlib only: ts, level, logger, msg,
    request_id (always present, "" when unset), and caller when known.
    The rendered message goes through redact_secrets so structured logs get
    the same secret hygiene as every persisted surface."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_secrets(message),
            "request_id": getattr(record, "request_id", "") or "",
        }
        caller = getattr(record, "caller", "") or ""
        if caller:
            payload["caller"] = caller
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


# Historical plain format plus the filter-provided rid suffix (empty string
# when no request id is bound, so lines without one are unchanged).
_PLAIN_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s%(rid_suffix)s: %(message)s"


def _log_format_mode() -> str:
    """UNIGROK_LOG_FORMAT: 'json' or 'plain'. Unset (or an unknown value)
    defaults to json on the cloudrun runtime — Cloud Logging ingests one
    JSON object per stdout/stderr line — and plain everywhere else."""
    raw = os.environ.get("UNIGROK_LOG_FORMAT", "").strip().lower()
    if raw in ("json", "plain"):
        return raw
    return "json" if is_cloudrun_runtime() else "plain"


# Setup rotating log handler globally
def setup_logging():
    logs_dir = PathResolver.get_logs_dir()
    log_file = logs_dir / "grok_mcp.log"

    # 10MB limit per file, rotate up to 5 backups
    rotating_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    stderr_handler = logging.StreamHandler(sys.stderr)
    handlers = [rotating_handler, stderr_handler]

    # The filter rides the handlers (not a logger) so every record emitted
    # anywhere in the process — including third-party loggers — carries the
    # request-id/caller attributes the formats below reference.
    context_filter = RequestContextLogFilter()
    for handler in handlers:
        handler.addFilter(context_filter)

    if _log_format_mode() == "json":
        formatter = JsonLogFormatter()
        for handler in handlers:
            handler.setFormatter(formatter)
        logging.basicConfig(level=logging.INFO, handlers=handlers)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format=_PLAIN_LOG_FORMAT,
            handlers=handlers
        )

# SQLite Session store to manage API thread IDs and CLI session mapping safely
# Concurrency write retry decorator for SQLite database lock safety
# Concurrency write retry decorator for SQLite database lock safety (async version)
_STALE_SQLITE_ERROR_TYPES = (
    sqlite3.InterfaceError,
    sqlite3.ProgrammingError,
    aiosqlite.ProgrammingError,
    ValueError,
    RuntimeError,
)


def _is_locked_sqlite_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _is_stale_sqlite_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    stale_markers = (
        "closed",
        "not open",
        "no active connection",
        "cannot operate on a closed database",
        "connection is closed",
    )
    return any(marker in msg for marker in stale_markers)


def _with_write_retry_async(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        retries = max(1, int(os.getenv("UNIGROK_DB_RETRIES", "12")))
        delay = float(os.getenv("UNIGROK_DB_RETRY_BASE", "0.05"))
        for attempt in range(retries):
            try:
                return await fn(*args, **kwargs)
            except (sqlite3.OperationalError, aiosqlite.OperationalError) as e:
                if _is_locked_sqlite_error(e) and attempt < retries - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2.0)
                    continue
                if _is_stale_sqlite_error(e) and args and hasattr(args[0], "_reset_connection"):
                    await args[0]._reset_connection()
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 2.0)
                        continue
                raise
            except _STALE_SQLITE_ERROR_TYPES as e:
                if _is_stale_sqlite_error(e) and args and hasattr(args[0], "_reset_connection"):
                    await args[0]._reset_connection()
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 2.0)
                        continue
                raise
    return wrapper


# Reads recover through the exact same passive machinery as writes: a
# stale/closed-connection error triggers _reset_connection (which also drops
# the read pool) and the call retries against fresh connections. There is no
# proactive health-check ping anywhere on the hot path.
_with_read_retry_async = _with_write_retry_async


# SQLite Session store to manage API thread IDs and CLI session mapping safely
class GrokSessionStore:
    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is not None:
            db_path = ":memory:" if str(db_path) == ":memory:" else Path(db_path)
        self._custom_db_path = db_path
        self._conn = None
        self._lock = asyncio.Lock()
        self._initialized = False
        # Read pool: independent read-only connections against the same WAL
        # db so reads never serialize through the write lock. _read_lock
        # guards only the checkout/lazy open, never the query itself.
        self._read_conns: Dict[int, Any] = {}
        self._read_lock = asyncio.Lock()
        self._read_rr = 0
        # knowledge_fts availability for THIS process/db pairing — set on
        # every init by _setup_knowledge_fts (FTS5 is a compile-time SQLite
        # option, so it is re-probed rather than persisted).
        self._knowledge_fts = False
        # task_memory_fts availability — same contract as _knowledge_fts.
        self._task_memory_fts = False

    @property
    def db_path(self) -> Path | str:
        if self._custom_db_path:
            return self._custom_db_path
        return PathResolver.get_chats_dir() / "grok_sessions.db"

    def _read_pool_size(self) -> int:
        try:
            return max(1, int(os.getenv("UNIGROK_READ_POOL_SIZE", "3")))
        except ValueError:
            return 3

    async def _close_read_pool(self):
        async with self._read_lock:
            conns = [conn for conn in self._read_conns.values() if conn is not None]
            self._read_conns = {}
            self._read_rr = 0
        for conn in conns:
            with contextlib.suppress(Exception):
                await conn.close()

    async def _checkout_read_conn(self):
        """Round-robin checkout of a pooled read-only connection."""
        async with self._read_lock:
            slot = self._read_rr % self._read_pool_size()
            self._read_rr += 1
            conn = self._read_conns.get(slot)
            if conn is None:
                conn = await aiosqlite.connect(self.db_path)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA busy_timeout=30000;")
                await conn.execute("PRAGMA query_only=ON;")
                self._read_conns[slot] = conn
            return conn

    @contextlib.asynccontextmanager
    async def _read_conn(self):
        """Yield a connection for a read-only query.

        Normally a pooled read connection with no lock held during the query,
        so reads interleave freely while a write holds the write lock. An
        in-memory db is per-connection, so it falls back to the shared write
        connection under the write lock.
        """
        if str(self.db_path) == ":memory:":
            async with self._lock:
                yield self._conn
            return
        yield await self._checkout_read_conn()

    async def _reset_connection_unlocked(self):
        conn = self._conn
        self._conn = None
        self._initialized = False
        if conn:
            with contextlib.suppress(Exception):
                await conn.close()
        await self._close_read_pool()

    async def _reset_connection(self):
        async with self._lock:
            await self._reset_connection_unlocked()

    async def _ensure_initialized(self):
        # Hot path: a bare flag check — no lock, no query. Recovery from
        # stale/closed connections is passive via the retry decorators.
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return
            await self._reset_connection_unlocked()

            if str(self.db_path) != ":memory:":
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute("PRAGMA synchronous=NORMAL;")
            await self._conn.execute("PRAGMA busy_timeout=30000;")
            await self._conn.execute("PRAGMA foreign_keys=ON;")

            # Initialize schema
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_name TEXT PRIMARY KEY,
                        cli_session_id TEXT,
                        api_thread_id TEXT,
                        last_active TEXT,
                        model TEXT
                    )
                """)
                await self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        intent TEXT,
                        chosen_plane TEXT,
                        success INTEGER,
                        latency REAL,
                        cost REAL,
                        context_id TEXT
                    )
                """)
                await self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_name TEXT,
                        role TEXT,
                        content TEXT,
                        timestamp TEXT,
                        FOREIGN KEY(session_name) REFERENCES sessions(session_name) ON DELETE CASCADE
                    )
                """)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

            # User version migrations
            async with self._conn.execute("PRAGMA user_version;") as cursor:
                row = await cursor.fetchone()
                version = row[0]

            if version < 1:
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("ALTER TABLE messages ADD COLUMN metadata TEXT DEFAULT NULL;")
                except aiosqlite.OperationalError:
                    pass
                await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_name, id ASC);")
                await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_intent ON telemetry(intent);")
                await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);")
                await self._conn.execute("PRAGMA user_version = 1;")
                await self._conn.commit()
                try:
                    await self._conn.execute("ANALYZE;")
                except aiosqlite.OperationalError:
                    pass

            if version < 2:
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("ALTER TABLE telemetry ADD COLUMN context_id TEXT DEFAULT NULL;")
                except aiosqlite.OperationalError:
                    pass
                await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_context_id ON telemetry(context_id);")
                await self._conn.execute("PRAGMA user_version = 2;")
                await self._conn.commit()

            if version < 3:
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS task_memory (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            task_hash TEXT,
                            prompt_terms TEXT,
                            prompt_excerpt TEXT,
                            outcome_summary TEXT,
                            plane TEXT,
                            model TEXT,
                            profile TEXT,
                            success INTEGER,
                            latency REAL,
                            cost REAL,
                            context_id TEXT,
                            created_at TEXT
                        )
                    """)
                    await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_task_memory_hash ON task_memory(task_hash);")
                    await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_task_memory_context_id ON task_memory(context_id);")
                    await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_task_memory_created_at ON task_memory(created_at);")
                    await self._conn.execute("PRAGMA user_version = 3;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            if version < 4:
                # Task-memory metadata (JSON text) — records per-task outcome
                # flags such as escalated=True so future retrieval sees which
                # tasks needed escalation to the planning model.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("ALTER TABLE task_memory ADD COLUMN metadata TEXT DEFAULT NULL;")
                except aiosqlite.OperationalError:
                    pass
                await self._conn.execute("PRAGMA user_version = 4;")
                await self._conn.commit()

            if version < 5:
                # Deferred research jobs (JobManager, src/jobs.py). Rows are
                # the durable record: they survive queries across the server's
                # lifetime, while the in-flight asyncio task that owns a job
                # does NOT survive a restart — a queued/running row whose
                # updated_at is older than the job timeout reads as 'stale'.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS jobs (
                            id TEXT PRIMARY KEY,
                            status TEXT NOT NULL DEFAULT 'queued',
                            prompt TEXT,
                            model TEXT,
                            created_at TEXT,
                            updated_at TEXT,
                            result TEXT,
                            cost REAL DEFAULT 0.0
                        )
                    """)
                    await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);")
                    await self._conn.execute("PRAGMA user_version = 5;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            if version < 6:
                # Eval-derived routing calibration (evals/ harness). One row
                # per (category, route, model) aggregated from golden-task
                # outcomes; fresh rows (updated_at within
                # UNIGROK_CALIBRATION_TTL_HOURS, default 168) with n >= 5 take
                # precedence over raw telemetry in RoutingAdvisor borderline
                # decisions — this table IS the eval → router closed loop.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS routing_calibration (
                            category TEXT NOT NULL,
                            route TEXT NOT NULL,
                            model TEXT NOT NULL,
                            success_rate REAL NOT NULL DEFAULT 0.0,
                            avg_cost_usd REAL NOT NULL DEFAULT 0.0,
                            n INTEGER NOT NULL DEFAULT 0,
                            updated_at TEXT,
                            PRIMARY KEY (category, route, model)
                        )
                    """)
                    await self._conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_routing_calibration_updated_at "
                        "ON routing_calibration(updated_at);"
                    )
                    await self._conn.execute("PRAGMA user_version = 6;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            if version < 7:
                # Local-first knowledge memory: distilled durable FACTS, not
                # transcripts. scope='global' facts inject everywhere; a
                # session-scoped fact carries its session name. terms mirrors
                # task_memory.prompt_terms (space-joined _task_terms) so the
                # LIKE/term-overlap fallback ranking works without FTS5.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    await self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS knowledge (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            scope TEXT NOT NULL DEFAULT 'global',
                            fact TEXT NOT NULL,
                            source TEXT,
                            terms TEXT,
                            created_at TEXT,
                            last_used_at TEXT,
                            uses INTEGER NOT NULL DEFAULT 0
                        )
                    """)
                    await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge(scope);")
                    await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_created_at ON knowledge(created_at);")
                    await self._conn.execute("PRAGMA user_version = 7;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            if version < 8:
                # Caller identity (multi-agent workspace): telemetry rows gain
                # a metadata JSON column ({"caller": ...}) plus created_at so
                # per-caller daily budgets and /metrics segmentation can scan
                # only today's rows via the created_at index; research-job
                # rows record the submitting caller directly.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    for ddl in (
                        "ALTER TABLE telemetry ADD COLUMN metadata TEXT DEFAULT NULL;",
                        "ALTER TABLE telemetry ADD COLUMN created_at TEXT DEFAULT NULL;",
                        "ALTER TABLE jobs ADD COLUMN caller TEXT DEFAULT NULL;",
                    ):
                        try:
                            await self._conn.execute(ddl)
                        except aiosqlite.OperationalError as ddl_err:
                            # Only a duplicate column (a prior partial run
                            # already added it) is benign; anything else must
                            # abort — stamping v8 with columns missing would
                            # brick every metadata write.
                            if "duplicate column" not in str(ddl_err).lower():
                                raise
                    await self._conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_telemetry_created_at ON telemetry(created_at);"
                    )
                    await self._conn.execute("PRAGMA user_version = 8;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            if version < 9:
                # Request correlation ids (observability): job rows record the
                # request id bound when the job was submitted so a gateway
                # X-Request-Id/traceparent can be traced into the deferred
                # work it spawned. Telemetry needs no DDL — its request_id
                # rides the v8 metadata JSON envelope.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    try:
                        await self._conn.execute(
                            "ALTER TABLE jobs ADD COLUMN request_id TEXT DEFAULT NULL;"
                        )
                    except aiosqlite.OperationalError as ddl_err:
                        # See v8: only duplicate-column reruns are benign.
                        if "duplicate column" not in str(ddl_err).lower():
                            raise
                    await self._conn.execute("PRAGMA user_version = 9;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            if version < 10:
                # Task-memory cloud mirror (UNIGROK_TASK_RAG): rows gain sync
                # bookkeeping so `synced_at IS NULL` IS the durable outbox —
                # no separate sync table, no claim/lease (single-process
                # server; backfill is resumable by construction).
                # remote_file_id maps xAI collection search hits back to
                # local rows; sync_error holds bounded-redacted diagnostics.
                await self._conn.execute("BEGIN IMMEDIATE;")
                try:
                    for ddl in (
                        "ALTER TABLE task_memory ADD COLUMN remote_file_id TEXT DEFAULT NULL;",
                        "ALTER TABLE task_memory ADD COLUMN synced_at TEXT DEFAULT NULL;",
                        "ALTER TABLE task_memory ADD COLUMN sync_attempts INTEGER NOT NULL DEFAULT 0;",
                        "ALTER TABLE task_memory ADD COLUMN sync_error TEXT DEFAULT NULL;",
                    ):
                        try:
                            await self._conn.execute(ddl)
                        except aiosqlite.OperationalError as ddl_err:
                            # See v8: only duplicate-column reruns are benign.
                            if "duplicate column" not in str(ddl_err).lower():
                                raise
                    await self._conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_task_memory_unsynced "
                        "ON task_memory(id) WHERE synced_at IS NULL;"
                    )
                    await self._conn.execute("PRAGMA user_version = 10;")
                    await self._conn.commit()
                except Exception:
                    await self._conn.rollback()
                    raise

            # knowledge_fts is (re)checked on EVERY init, not just inside the
            # v7 gate: FTS5 is a compile-time SQLite option, so a db created
            # on a build WITH it can be reopened by one WITHOUT it (and vice
            # versa). Never fatal — False just routes search_facts through
            # the LIKE/term-overlap fallback.
            self._knowledge_fts = await self._setup_knowledge_fts()
            self._task_memory_fts = await self._setup_task_memory_fts()

            self._initialized = True

    @staticmethod
    async def _probe_fts5(conn) -> bool:
        """Capability probe: can this SQLite build create an FTS5 virtual
        table? Probed with a TEMP virtual table so nothing persists. Tests
        monkeypatch this to force the fallback ranking path."""
        try:
            await conn.execute("CREATE VIRTUAL TABLE temp.__unigrok_fts5_probe__ USING fts5(probe);")
            await conn.execute("DROP TABLE temp.__unigrok_fts5_probe__;")
            return True
        except Exception:
            return False

    async def _setup_knowledge_fts(self) -> bool:
        """Create/repair the knowledge_fts index; returns its availability.

        knowledge_fts is a REGULAR fts5 table (rowid = knowledge.id, own copy
        of fact/terms) kept in sync by dual-writes in save_fact/delete_fact —
        deliberately no triggers, so plain knowledge writes never break on a
        build without FTS5. A diverged index — facts with no index entry
        and/or orphaned index entries (writes made while FTS5 was
        unavailable, or a db moved across builds) — triggers a full rebuild
        here at init time. Divergence is checked row-by-row on ids, NOT by
        comparing counts: an unindexed insert plus an unindexed delete cancel
        out in the totals while both rows are still wrong. Fact counts are
        small (distilled facts, not transcripts), so the check and the
        rebuild are cheap."""
        if not await self._probe_fts5(self._conn):
            return False
        try:
            await self._conn.execute("BEGIN IMMEDIATE;")
            await self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(fact, terms);"
            )
            async with self._conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM knowledge WHERE id NOT IN (SELECT rowid FROM knowledge_fts)) "
                "+ (SELECT COUNT(*) FROM knowledge_fts WHERE rowid NOT IN (SELECT id FROM knowledge))"
            ) as cursor:
                row = await cursor.fetchone()
                n_diverged = int(row[0] if row else 0)
            if n_diverged:
                await self._conn.execute("DELETE FROM knowledge_fts;")
                await self._conn.execute(
                    "INSERT INTO knowledge_fts(rowid, fact, terms) SELECT id, fact, terms FROM knowledge;"
                )
            await self._conn.commit()
            return True
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._conn.rollback()
            logging.getLogger("GrokMCP").warning(
                f"knowledge_fts unavailable; using term-overlap fallback ranking: {exc}"
            )
            return False

    async def _setup_task_memory_fts(self) -> bool:
        """Create/repair the task_memory_fts index; returns its availability.

        Same contract as _setup_knowledge_fts: a REGULAR fts5 table
        (rowid = task_memory.id, own copy of prompt_terms/prompt_excerpt)
        kept in sync by a dual-write in save_task_memory — no triggers, so
        plain saves never break on a build without FTS5. Divergence is
        checked row-by-row on ids and repaired with a full rebuild.
        task_memory rows are never deleted (no delete method exists), so
        unlike knowledge there is no delete path to mirror."""
        if not await self._probe_fts5(self._conn):
            return False
        try:
            await self._conn.execute("BEGIN IMMEDIATE;")
            await self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS task_memory_fts USING fts5(prompt_terms, prompt_excerpt);"
            )
            async with self._conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM task_memory WHERE id NOT IN (SELECT rowid FROM task_memory_fts)) "
                "+ (SELECT COUNT(*) FROM task_memory_fts WHERE rowid NOT IN (SELECT id FROM task_memory))"
            ) as cursor:
                row = await cursor.fetchone()
                n_diverged = int(row[0] if row else 0)
            if n_diverged:
                await self._conn.execute("DELETE FROM task_memory_fts;")
                await self._conn.execute(
                    "INSERT INTO task_memory_fts(rowid, prompt_terms, prompt_excerpt) "
                    "SELECT id, prompt_terms, prompt_excerpt FROM task_memory;"
                )
            await self._conn.commit()
            return True
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._conn.rollback()
            logging.getLogger("GrokMCP").warning(
                f"task_memory_fts unavailable; using term-overlap fallback ranking: {exc}"
            )
            return False

    async def close(self):
        # Close readers first so the truncating checkpoint below is not
        # blocked by pooled read snapshots.
        await self._close_read_pool()
        async with self._lock:
            if self._conn:
                try:
                    await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                except Exception:
                    pass
                await self._conn.close()
                self._conn = None
            self._initialized = False

    async def _on_write_completed(self):
        if str(self.db_path) == ":memory:":
            return
        try:
            wal_path = Path(str(self.db_path) + "-wal")
            if wal_path.exists():
                size_mb = wal_path.stat().st_size / (1024 * 1024)
                if size_mb > 50.0:
                    await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                else:
                    self._tx_count = getattr(self, "_tx_count", 0) + 1
                    if self._tx_count % 20 == 0:
                        await self._conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except Exception as e:
            logging.getLogger("GrokMCP").warning(f"Failed to execute WAL checkpoint logic: {e}")

    async def vacuum_db(self):
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("VACUUM;")

    @_with_write_retry_async
    async def save_telemetry(
        self,
        intent: str,
        chosen_plane: str,
        success: int,
        latency: float,
        cost: float,
        context_id: Optional[str] = None,
        caller: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        await self._ensure_initialized()
        # caller and request_id ride the metadata JSON column (v8) so future
        # per-row attributes extend the same envelope without another
        # migration. Both fall back to the values bound to the current async
        # context (orchestrate/gateway) when the params are None — the
        # src/storage.py contract — so indirect writers (run_thinking_loop,
        # maybe_compact_history) stay attributed without threading params.
        meta: Dict[str, Any] = {}
        clean_caller = normalize_caller(caller) or get_active_caller()
        if clean_caller:
            meta["caller"] = clean_caller
        clean_request_id = normalize_request_id(request_id) or get_request_id()
        if clean_request_id:
            meta["request_id"] = clean_request_id
        meta_str = json.dumps(meta, separators=(",", ":")) if meta else None
        now_str = datetime.now().isoformat()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute(
                    "INSERT INTO telemetry (intent, chosen_plane, success, latency, cost, context_id, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (intent, chosen_plane, success, latency, cost, context_id, meta_str, now_str)
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_read_retry_async
    async def get_telemetry_stats(self) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute("SELECT * FROM telemetry ORDER BY id DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @_with_read_retry_async
    async def get_caller_cost_today(self, caller_substring: str) -> float:
        """Today's total telemetry cost attributed to callers matching the
        (case-insensitive) substring — the per-caller budget pot.

        One indexed read: idx_telemetry_created_at bounds the scan to today's
        rows; the caller match runs in Python over that bounded slice so the
        query never depends on the optional json1 extension."""
        await self._ensure_initialized()
        needle = str(caller_substring or "").strip().lower()
        if not needle:
            return 0.0
        # ISO timestamps compare lexicographically: '2026-07-02T...' sorts
        # after the bare '2026-07-02' date and after every earlier day.
        day_start = datetime.now().date().isoformat()
        async with self._read_conn() as conn:
            async with conn.execute(
                "SELECT cost, metadata FROM telemetry WHERE created_at >= ?",
                (day_start,),
            ) as cursor:
                rows = await cursor.fetchall()
        total = 0.0
        for row in rows:
            caller = telemetry_row_caller(dict(row))
            if caller and needle in caller.lower() and row["cost"] is not None:
                total += float(row["cost"])
        return total

    @_with_read_retry_async
    async def get_caller_stats_today(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Per-caller aggregate over TODAY's telemetry rows, busiest first:
        {caller, requests, success_rate, total_cost_usd}. Unattributed rows
        (pre-v8 or anonymous) are excluded. Same bounded created_at-indexed
        read as get_caller_cost_today — consumed by grok_mcp_status."""
        await self._ensure_initialized()
        bounded = max(1, min(int(limit or 10), 50))
        day_start = datetime.now().date().isoformat()
        async with self._read_conn() as conn:
            async with conn.execute(
                "SELECT success, cost, metadata FROM telemetry WHERE created_at >= ?",
                (day_start,),
            ) as cursor:
                rows = await cursor.fetchall()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            entry = dict(row)
            caller = telemetry_row_caller(entry)
            if caller:
                grouped.setdefault(caller, []).append(entry)
        stats = []
        for caller, entries in grouped.items():
            successes = sum(1 for entry in entries if entry.get("success") == 1)
            stats.append({
                "caller": caller,
                "requests": len(entries),
                "success_rate": round(successes / len(entries), 4),
                "total_cost_usd": round(
                    sum(float(entry["cost"]) for entry in entries if entry.get("cost") is not None), 6
                ),
            })
        stats.sort(key=lambda item: (-item["requests"], item["caller"]))
        return stats[:bounded]

    @_with_read_retry_async
    async def get_recent_model_stats(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Per-plane/model aggregate over the most recent task-memory rows.

        The RoutingAdvisor's data source: rows are
        {plane, model, samples, success_rate, avg_cost} computed over the last
        `limit` task_memory entries (task memory carries the model column;
        the telemetry table only records the plane).
        """
        await self._ensure_initialized()
        bounded = max(1, min(int(limit or 200), 1000))
        async with self._read_conn() as conn:
            async with conn.execute(
                """
                SELECT plane, model,
                       COUNT(*) AS samples,
                       AVG(success) AS success_rate,
                       AVG(cost) AS avg_cost
                FROM (
                    SELECT plane, model, success, cost
                    FROM task_memory ORDER BY id DESC LIMIT ?
                )
                GROUP BY plane, model
                """,
                (bounded,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @_with_write_retry_async
    async def upsert_routing_calibration(
        self,
        category: str,
        route: str,
        model: str,
        success_rate: float,
        avg_cost_usd: float,
        n: int,
    ):
        """Upsert one eval-derived calibration row (evals/runner.py writes
        these after every run). updated_at always bumps so the freshness
        window in get_routing_calibration measures the last eval run."""
        await self._ensure_initialized()
        now_str = datetime.now().isoformat()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute(
                    """
                    INSERT INTO routing_calibration
                        (category, route, model, success_rate, avg_cost_usd, n, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(category, route, model) DO UPDATE SET
                        success_rate = excluded.success_rate,
                        avg_cost_usd = excluded.avg_cost_usd,
                        n = excluded.n,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(category or "unknown"),
                        str(route or "unknown"),
                        str(model or "unknown"),
                        float(success_rate or 0.0),
                        float(avg_cost_usd or 0.0),
                        int(n or 0),
                        now_str,
                    ),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_read_retry_async
    async def get_routing_calibration(
        self, max_age_hours: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Calibration rows, optionally filtered to those refreshed within the
        last max_age_hours (ISO timestamps compare lexicographically, matching
        the jobs-staleness convention)."""
        await self._ensure_initialized()
        query = "SELECT * FROM routing_calibration"
        params: tuple = ()
        if max_age_hours is not None:
            cutoff = (datetime.now() - timedelta(hours=float(max_age_hours))).isoformat()
            query += " WHERE updated_at >= ?"
            params = (cutoff,)
        query += " ORDER BY category, route, model"
        async with self._read_conn() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @_with_write_retry_async
    async def save_task_memory(
        self,
        prompt: str,
        outcome_summary: str,
        plane: str,
        model: str,
        profile: str,
        success: int,
        latency: float,
        cost: float,
        context_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        await self._ensure_initialized()
        terms = _task_terms(prompt)
        prompt_terms_text = " ".join(terms)
        prompt_excerpt = _bounded_redacted(prompt, 500)
        now_str = datetime.now().isoformat()
        metadata_json = None
        if metadata:
            try:
                metadata_json = json.dumps(metadata, separators=(",", ":"), ensure_ascii=False)
            except (TypeError, ValueError) as meta_err:
                logging.getLogger("GrokMCP").warning(f"Task memory metadata not serializable: {meta_err}")
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = await self._conn.execute(
                    """
                    INSERT INTO task_memory (
                        task_hash, prompt_terms, prompt_excerpt, outcome_summary,
                        plane, model, profile, success, latency, cost, context_id, created_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _task_hash(prompt),
                        prompt_terms_text,
                        prompt_excerpt,
                        _bounded_redacted(outcome_summary, 1000),
                        str(plane or "unknown"),
                        str(model or "unknown"),
                        str(profile or "unknown"),
                        int(1 if success else 0),
                        float(latency or 0.0),
                        float(cost or 0.0),
                        context_id,
                        now_str,
                        metadata_json,
                    ),
                )
                memory_id = int(cursor.lastrowid)
                if self._task_memory_fts:
                    # Dual-write into the index — no triggers by design (see
                    # _setup_task_memory_fts). A failing index write degrades
                    # to fallback ranking, never fails the save.
                    try:
                        await self._conn.execute(
                            "INSERT INTO task_memory_fts(rowid, prompt_terms, prompt_excerpt) VALUES (?, ?, ?)",
                            (memory_id, prompt_terms_text, prompt_excerpt),
                        )
                    except Exception as fts_err:
                        self._task_memory_fts = False
                        logging.getLogger("GrokMCP").warning(
                            f"task_memory_fts write failed; fallback ranking engaged: {fts_err}"
                        )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()
        return memory_id

    @_with_read_retry_async
    async def get_similar_task_memories(
        self,
        prompt: str,
        context_id: Optional[str] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Rank stored task memories against a prompt; every row carries a
        `score` (higher = better on both paths).

        FTS5 path: MATCH over an OR-joined _task_terms expression (safe
        tokens only — raw prompt text never reaches MATCH), bm25-ranked and
        batch-normalized into the same 0..1 band as the fallback's
        term-overlap fraction BEFORE the +2.0 context_id / +1.0 task_hash
        bonuses, so downstream consumers see one score contract. A plain
        context_id query is merged in because a same-context row with ZERO
        term overlap must still surface (score 0 + 2.0 bonus — the
        long-standing fallback semantics). Fallback (no FTS5): Python
        term-overlap over the most recent 200 rows."""
        await self._ensure_initialized()
        prompt_terms = set(_task_terms(prompt))
        prompt_hash = _task_hash(prompt)
        bounded = max(1, min(int(limit or 3), 10))

        if self._task_memory_fts and prompt_terms:
            match_expr = " OR ".join(sorted(prompt_terms))
            async with self._read_conn() as conn:
                async with conn.execute(
                    "SELECT t.*, bm25(task_memory_fts) AS fts_rank FROM task_memory_fts "
                    "JOIN task_memory t ON t.id = task_memory_fts.rowid "
                    "WHERE task_memory_fts MATCH ? ORDER BY fts_rank LIMIT 50",
                    (match_expr,),
                ) as cursor:
                    fts_rows = await cursor.fetchall()
                context_rows: List[Any] = []
                if context_id:
                    async with conn.execute(
                        "SELECT * FROM task_memory WHERE context_id = ? "
                        "ORDER BY id DESC LIMIT 10",
                        (context_id,),
                    ) as cursor:
                        context_rows = await cursor.fetchall()

            candidates: Dict[int, Dict[str, Any]] = {}
            for row in fts_rows:
                item = self._decode_task_memory_row(row)
                item["_fts_raw"] = max(0.0, -float(item.pop("fts_rank", 0.0) or 0.0))
                candidates[int(item["id"])] = item
            for row in context_rows:
                item = self._decode_task_memory_row(row)
                candidates.setdefault(int(item["id"]), item)

            max_raw = max(
                (item.get("_fts_raw", 0.0) for item in candidates.values()),
                default=0.0,
            )
            scored = []
            for item in candidates.values():
                raw = float(item.pop("_fts_raw", 0.0))
                score = raw / max_raw if max_raw > 0 else 0.0
                if context_id and item.get("context_id") == context_id:
                    score += 2.0
                if item.get("task_hash") == prompt_hash:
                    score += 1.0
                if score <= 0:
                    continue
                item["score"] = score
                scored.append(item)
            scored.sort(key=lambda item: (float(item["score"]), int(item["id"])), reverse=True)
            return scored[:bounded]

        async with self._read_conn() as conn:
            async with conn.execute(
                "SELECT * FROM task_memory ORDER BY id DESC LIMIT 200"
            ) as cursor:
                rows = await cursor.fetchall()

        scored = []
        for row in rows:
            item = self._decode_task_memory_row(row)
            row_terms = set(str(item.get("prompt_terms") or "").split())
            overlap = len(prompt_terms & row_terms)
            score = 0.0
            if prompt_terms:
                score = overlap / max(len(prompt_terms), 1)
            if context_id and item.get("context_id") == context_id:
                score += 2.0
            if item.get("task_hash") == prompt_hash:
                score += 1.0
            if score <= 0:
                continue
            item["score"] = score
            scored.append(item)

        scored.sort(key=lambda item: (float(item["score"]), int(item["id"])), reverse=True)
        return scored[:bounded]

    @_with_read_retry_async
    async def get_task_memory_count(self) -> int:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute("SELECT COUNT(*) FROM task_memory") as cursor:
                row = await cursor.fetchone()
                return int(row[0] if row else 0)

    @staticmethod
    def _decode_task_memory_row(row: Any) -> Dict[str, Any]:
        item = dict(row)
        # metadata is stored as JSON text — hand callers a dict.
        meta_raw = item.get("metadata")
        if isinstance(meta_raw, str) and meta_raw:
            try:
                item["metadata"] = json.loads(meta_raw)
            except (TypeError, ValueError):
                pass
        return item

    # ── Task-memory cloud-mirror outbox (UNIGROK_TASK_RAG, v10) ─────────────
    # `synced_at IS NULL` IS the outbox; there is no separate sync table.

    @_with_read_retry_async
    async def list_unsynced_task_memories(
        self,
        limit: int = 50,
        max_attempts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        bounded = max(1, min(int(limit or 50), 200))
        query = "SELECT * FROM task_memory WHERE synced_at IS NULL"
        params: List[Any] = []
        if max_attempts is not None:
            query += " AND sync_attempts < ?"
            params.append(int(max_attempts))
        query += " ORDER BY id ASC LIMIT ?"
        params.append(bounded)
        async with self._read_conn() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [self._decode_task_memory_row(row) for row in rows]

    @_with_write_retry_async
    async def mark_task_memory_synced(self, memory_id: int, remote_file_id: str) -> None:
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute(
                    "UPDATE task_memory SET synced_at = ?, remote_file_id = ?, sync_error = NULL "
                    "WHERE id = ?",
                    (datetime.now().isoformat(), str(remote_file_id or ""), int(memory_id)),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_write_retry_async
    async def mark_task_memory_sync_failed(self, memory_id: int, error: str) -> None:
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute(
                    "UPDATE task_memory SET sync_attempts = sync_attempts + 1, sync_error = ? "
                    "WHERE id = ?",
                    (_bounded_redacted(str(error or ""), 500), int(memory_id)),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_read_retry_async
    async def get_task_memories_by_remote_ids(
        self, file_ids: List[str]
    ) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        wanted = [str(fid) for fid in (file_ids or []) if str(fid or "").strip()][:25]
        if not wanted:
            return []
        placeholders = ", ".join("?" for _ in wanted)
        async with self._read_conn() as conn:
            async with conn.execute(
                f"SELECT * FROM task_memory WHERE remote_file_id IN ({placeholders})",
                wanted,
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._decode_task_memory_row(row) for row in rows]

    @_with_read_retry_async
    async def count_unsynced_task_memories(self) -> int:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM task_memory WHERE synced_at IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0] if row else 0)

    @_with_write_retry_async
    async def reset_task_memory_sync(self) -> int:
        """Re-queue EVERY task memory for mirroring (rag backfill
        --force-reupload): deterministic document names keep the re-upload
        idempotent on the collection side. Returns the row count."""
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = await self._conn.execute(
                    "UPDATE task_memory SET synced_at = NULL, sync_attempts = 0, sync_error = NULL"
                )
                count = int(cursor.rowcount or 0)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()
        return count

    # pattern_cache (get_cached_pattern/save_cached_pattern) removed — the
    # table was write-only with zero callers. Task memory (task_memory table)
    # is the surviving learning store.

    # ── Local-first knowledge memory (knowledge + optional knowledge_fts) ────
    @_with_write_retry_async
    async def save_fact(
        self,
        fact: str,
        scope: str = "global",
        source: str = "",
    ) -> Optional[int]:
        """Persist one distilled fact, redacted and bounded at rest.

        Deduped on exact (scope, fact) text: re-saving an existing fact
        touches it (uses+1, last_used_at bump) instead of inserting a
        duplicate, so re-distilling a session never multiplies rows. Returns
        the row id (existing or new); None for empty facts.
        """
        await self._ensure_initialized()
        text = _bounded_redacted(str(fact or ""), 1000)
        if not text:
            return None
        # scope is client-controlled (remember_fact): redacted and bounded at
        # rest like fact/source (see _normalize_fact_scope).
        scope_value = _normalize_fact_scope(scope)
        source_value = _bounded_redacted(str(source or ""), 200)
        terms = " ".join(_task_terms(text))
        now_str = datetime.now().isoformat()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                async with self._conn.execute(
                    "SELECT id FROM knowledge WHERE scope = ? AND fact = ?",
                    (scope_value, text),
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing:
                    fact_id = int(existing[0])
                    await self._conn.execute(
                        "UPDATE knowledge SET last_used_at = ?, uses = uses + 1 WHERE id = ?",
                        (now_str, fact_id),
                    )
                else:
                    cursor = await self._conn.execute(
                        "INSERT INTO knowledge (scope, fact, source, terms, created_at, last_used_at, uses) "
                        "VALUES (?, ?, ?, ?, ?, ?, 0)",
                        (scope_value, text, source_value, terms, now_str, now_str),
                    )
                    fact_id = int(cursor.lastrowid)
                    if self._knowledge_fts:
                        # Dual-write into the index — no triggers by design
                        # (see _setup_knowledge_fts). A failing index write
                        # degrades to fallback ranking, never fails the fact.
                        try:
                            await self._conn.execute(
                                "INSERT INTO knowledge_fts(rowid, fact, terms) VALUES (?, ?, ?)",
                                (fact_id, text, terms),
                            )
                        except Exception as fts_err:
                            self._knowledge_fts = False
                            logging.getLogger("GrokMCP").warning(
                                f"knowledge_fts write failed; fallback ranking engaged: {fts_err}"
                            )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()
        return fact_id

    @_with_read_retry_async
    async def search_facts(
        self,
        query: str,
        scope: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Rank stored facts against a query; every row carries a `score`
        (higher = better on both paths).

        FTS5 path: MATCH over an OR-joined _task_terms expression (safe
        tokens only — raw query text never reaches MATCH), ranked by bm25.
        Fallback path (no FTS5): LIKE prefilter on the terms column plus
        term-overlap scoring, like get_similar_task_memories. scope=None
        searches all scopes; a named scope also surfaces 'global' facts so
        session-scoped retrieval still sees workspace-wide knowledge.
        """
        await self._ensure_initialized()
        bounded = max(1, min(int(limit or 5), 25))
        query_terms = _task_terms(query)
        if not query_terms:
            return []
        scopes: Optional[tuple] = None
        if scope:
            # Same normalization as save_fact so scoped lookups keep matching
            # what a bounded save actually stored.
            scope_value = _normalize_fact_scope(scope)
            scopes = ("global",) if scope_value == "global" else (scope_value, "global")

        if self._knowledge_fts:
            match_expr = " OR ".join(query_terms)
            sql = (
                "SELECT k.*, bm25(knowledge_fts) AS fts_rank FROM knowledge_fts "
                "JOIN knowledge k ON k.id = knowledge_fts.rowid "
                "WHERE knowledge_fts MATCH ?"
            )
            params: List[Any] = [match_expr]
            if scopes:
                sql += f" AND k.scope IN ({', '.join('?' for _ in scopes)})"
                params.extend(scopes)
            sql += " ORDER BY fts_rank LIMIT ?"
            params.append(bounded)
            async with self._read_conn() as conn:
                async with conn.execute(sql, tuple(params)) as cursor:
                    rows = await cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                # bm25 is lower-is-better; expose higher-is-better like the
                # fallback path so callers rank uniformly.
                item["score"] = -float(item.pop("fts_rank", 0.0) or 0.0)
                results.append(item)
            return results

        # Fallback: LIKE prefilter (first 8 terms) narrows candidates, then
        # exact term-overlap scoring ranks them.
        like_terms = query_terms[:8]
        padded_terms = like_terms + ([""] * (8 - len(like_terms)))
        sql = (
            "SELECT * FROM knowledge WHERE ("
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%') OR "
            "(? != '' AND terms LIKE '%' || ? || '%'))"
        )
        params = [value for term in padded_terms for value in (term, term)]
        if scopes:
            if len(scopes) == 1:
                sql += " AND scope = ?"
                params.append(scopes[0])
            else:
                sql += " AND scope IN (?, ?)"
                params.extend(scopes)
        sql += " ORDER BY id DESC LIMIT 400"
        async with self._read_conn() as conn:
            async with conn.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        query_set = set(query_terms)
        scored = []
        for row in rows:
            item = dict(row)
            row_terms = set(str(item.get("terms") or "").split())
            overlap = len(query_set & row_terms)
            if overlap <= 0:
                continue
            item["score"] = overlap / max(len(query_set), 1)
            scored.append(item)
        scored.sort(key=lambda item: (float(item["score"]), int(item["id"])), reverse=True)
        return scored[:bounded]

    @_with_write_retry_async
    async def touch_facts(self, fact_ids: List[int]):
        """Bump uses/last_used_at on the given facts (called for every fact
        actually injected into a prompt)."""
        ids = []
        for value in fact_ids or []:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))
        if not ids:
            return
        await self._ensure_initialized()
        now_str = datetime.now().isoformat()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.executemany(
                    "UPDATE knowledge SET last_used_at = ?, uses = uses + 1 WHERE id = ?",
                    [(now_str, fact_id) for fact_id in ids],
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_write_retry_async
    async def delete_fact(self, fact_id: int) -> bool:
        """Remove one fact (and its index row); True when a row was deleted."""
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = await self._conn.execute(
                    "DELETE FROM knowledge WHERE id = ?", (int(fact_id),)
                )
                deleted = bool(cursor.rowcount)
                if self._knowledge_fts:
                    with contextlib.suppress(Exception):
                        await self._conn.execute(
                            "DELETE FROM knowledge_fts WHERE rowid = ?", (int(fact_id),)
                        )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()
        return deleted

    @_with_read_retry_async
    async def count_facts(self) -> int:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute("SELECT COUNT(*) FROM knowledge") as cursor:
                row = await cursor.fetchone()
                return int(row[0] if row else 0)

    @_with_read_retry_async
    async def list_facts(self, limit: int = 20, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        """Most recent facts first (the grok://knowledge resource view)."""
        await self._ensure_initialized()
        bounded = max(1, min(int(limit or 20), 100))
        sql = "SELECT * FROM knowledge"
        params: tuple = ()
        if scope:
            sql += " WHERE scope = ?"
            params = (_normalize_fact_scope(scope),)
        sql += " ORDER BY id DESC LIMIT ?"
        params = params + (bounded,)
        async with self._read_conn() as conn:
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @_with_read_retry_async
    async def get_session(self, session_name: str) -> Optional[Dict[str, Any]]:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute("SELECT * FROM sessions WHERE session_name = ?", (session_name,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None

    @_with_write_retry_async
    async def save_session(self, session_name: str, cli_session_id: str = None, api_thread_id: str = None, model: str = None):
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                async with self._conn.execute("SELECT 1 FROM sessions WHERE session_name = ?", (session_name,)) as cursor:
                    exists = await cursor.fetchone()
                now_str = datetime.now().isoformat()
                if exists:
                    await self._conn.execute(
                        "UPDATE sessions SET "
                        "cli_session_id = COALESCE(?, cli_session_id), "
                        "api_thread_id = COALESCE(?, api_thread_id), "
                        "model = COALESCE(?, model), "
                        "last_active = ? WHERE session_name = ?",
                        (cli_session_id, api_thread_id, model, now_str, session_name),
                    )
                else:
                    await self._conn.execute(
                        "INSERT INTO sessions (session_name, cli_session_id, api_thread_id, last_active, model) VALUES (?, ?, ?, ?, ?)",
                        (session_name, cli_session_id, api_thread_id, now_str, model)
                    )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_write_retry_async
    async def delete_session(self, session_name: str):
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute("DELETE FROM sessions WHERE session_name = ?", (session_name,))
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_read_retry_async
    async def list_sessions(self) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute("SELECT * FROM sessions ORDER BY last_active DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @_with_write_retry_async
    async def save_message(self, session_name: str, role: str, content: str, metadata: Optional[dict] = None):
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                async with self._conn.execute("SELECT 1 FROM sessions WHERE session_name = ?", (session_name,)) as cursor:
                    exists = await cursor.fetchone()
                now_str = datetime.now().isoformat()
                if not exists:
                    await self._conn.execute(
                        "INSERT INTO sessions (session_name, last_active) VALUES (?, ?)",
                        (session_name, now_str)
                    )
                meta_str = json.dumps(metadata, separators=(',', ':')) if metadata else None
                await self._conn.execute(
                    "INSERT INTO messages (session_name, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                    (session_name, role, content, now_str, meta_str)
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_write_retry_async
    async def replace_messages(self, session_name: str, messages: List[Dict[str, Any]]):
        """Atomically replace a session's message history in one transaction.

        Replaces the old delete-then-reinsert save_history flow: a crash mid-way
        can no longer leave a session with partially rewritten history. The
        session row itself (cli_session_id/api_thread_id/model) is preserved.
        """
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute("DELETE FROM messages WHERE session_name = ?", (session_name,))
                async with self._conn.execute("SELECT 1 FROM sessions WHERE session_name = ?", (session_name,)) as cursor:
                    exists = await cursor.fetchone()
                now_str = datetime.now().isoformat()
                if not exists:
                    await self._conn.execute(
                        "INSERT INTO sessions (session_name, last_active) VALUES (?, ?)",
                        (session_name, now_str)
                    )
                else:
                    await self._conn.execute(
                        "UPDATE sessions SET last_active = ? WHERE session_name = ?",
                        (now_str, session_name)
                    )
                for msg in messages:
                    metadata = msg.get("metadata")
                    meta_str = json.dumps(metadata, separators=(',', ':')) if metadata else None
                    await self._conn.execute(
                        "INSERT INTO messages (session_name, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                        (session_name, msg.get("role"), msg.get("content"), now_str, meta_str)
                    )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_read_retry_async
    async def load_messages(self, session_name: str) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute(
                "SELECT role, content, timestamp, metadata FROM messages WHERE session_name = ? ORDER BY id ASC",
                (session_name,)
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for r in rows:
                    meta = None
                    if r["metadata"]:
                        try:
                            meta = json.loads(r["metadata"])
                        except Exception:
                            pass
                    msg = {
                        "role": r["role"],
                        "content": r["content"],
                        "time": r["timestamp"]
                    }
                    if meta is not None:
                        msg["metadata"] = meta
                    results.append(msg)
                return results

    # ── Deferred research jobs (consumed by src/jobs.py JobManager) ──────────
    @_with_write_retry_async
    async def create_job(
        self,
        job_id: str,
        prompt: str,
        model: str,
        caller: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Insert a 'queued' job row. caller=None and request_id=None fall
        back to the identities bound to the current async context (the
        src/storage.py contract) so gateway-submitted jobs stay attributed
        and traceable back to their originating request."""
        await self._ensure_initialized()
        now_str = datetime.now().isoformat()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute(
                    "INSERT INTO jobs (id, status, prompt, model, created_at, updated_at, result, cost, caller, request_id) "
                    "VALUES (?, 'queued', ?, ?, ?, ?, NULL, 0.0, ?, ?)",
                    (
                        str(job_id),
                        _bounded_redacted(prompt, 2000),
                        str(model or "unknown"),
                        now_str,
                        now_str,
                        normalize_caller(caller) or get_active_caller(),
                        normalize_request_id(request_id) or get_request_id() or None,
                    ),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_write_retry_async
    async def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        result: Optional[str] = None,
        cost: Optional[float] = None,
    ):
        """Update a job row; updated_at always bumps so staleness detection
        (JobManager) measures the last time the owning task touched the row."""
        await self._ensure_initialized()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                await self._conn.execute(
                    "UPDATE jobs SET updated_at = ?, "
                    "status = COALESCE(?, status), "
                    "result = COALESCE(?, result), "
                    "cost = COALESCE(?, cost) WHERE id = ?",
                    (
                        datetime.now().isoformat(),
                        str(status) if status is not None else None,
                        str(result) if result is not None else None,
                        float(cost) if cost is not None else None,
                        str(job_id),
                    ),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
            await self._on_write_completed()

    @_with_read_retry_async
    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_initialized()
        async with self._read_conn() as conn:
            async with conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @_with_read_retry_async
    async def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        bounded = max(1, min(int(limit or 20), 100))
        async with self._read_conn() as conn:
            async with conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (bounded,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]



# Global session and telemetry store singleton, created through the storage
# factory (src/storage.py) so UNIGROK_STORAGE_BACKEND selects the backend.
# The late import is deliberate: storage.get_store() imports GrokSessionStore
# back out of this (still-initializing) module, which works because the class
# is fully defined above this line.
from .storage import get_store as _get_store_factory

store = _get_store_factory()


# Asynchronous Context Manager for MCP tool execution wrapping
class GrokInvocationContext:
    def __init__(self, model: str, logger: logging.Logger, is_cli: bool = False, append_signature: bool = True):
        self.model = model
        self.logger = logger
        self.is_cli = is_cli
        self.append_signature = append_signature
        self.start_time = None
        self.elapsed = 0.0
        self.context_injected = False
        self.plane = "CLI" if is_cli else "API"
        self.fallback_occurred = False
        self.finish_reason = ""  # Optional MetaLayer.finish_reason for the footer

    async def __aenter__(self):
        self.start_time = datetime.now()
        self.logger.info(f"Starting MCP tool invocation: model={self.model}, is_cli={self.is_cli}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = (datetime.now() - self.start_time).total_seconds()
        if exc_type:
            self.logger.error(f"MCP tool invocation failed after {self.elapsed:.2f}s: {exc_val}", exc_info=(exc_type, exc_val, exc_tb))
        else:
            self.logger.info(f"MCP tool invocation completed successfully in {self.elapsed:.2f}s")
        return False # Propagate exceptions

    def format_output(self, base_content: str, usage_responses: list = None) -> str:
        footer = ""
        if usage_responses:
            footer = usage_footer(*usage_responses)

        final_text = (base_content + footer).rstrip()

        # The branded footer is OFF by default — downstream agents were
        # ingesting it as content. Cost/usage stays available via MetaLayer;
        # opt in explicitly with GROK_MCP_ENABLE_SIGNATURE=1.
        enable_sig = os.getenv("GROK_MCP_ENABLE_SIGNATURE", "").lower() in ("true", "1", "yes") or \
                     os.getenv("ENABLE_SIGNATURE", "").lower() in ("true", "1", "yes")
        suppress_sig = os.getenv("GROK_MCP_SUPPRESS_SIGNATURE", "").lower() in ("true", "1", "yes") or \
                       os.getenv("SUPPRESS_SIGNATURE", "").lower() in ("true", "1", "yes")
        if self.append_signature and enable_sig and not suppress_sig:
            client_type = self.plane
            ctx_str = "yes" if self.context_injected else "no"
            fallback_str = " (Fallback)" if self.fallback_occurred else ""
            outcome_str = f" • Outcome: {self.finish_reason}" if self.finish_reason else ""

            sig = (
                "\n\n───\n"
                f"Used: {self.model} ({client_type}{fallback_str}) • {self.elapsed:.1f}s • Context: {ctx_str}{outcome_str}\n"
                "Cooperative @grok teammate • Ready for Gemini/Claude to build on this"
            )
            return final_text + sig

        return final_text

# Encoding utilities
def encode_image_to_base64(image_path: str, max_bytes: Optional[int] = None) -> str:
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise ValueError(f"Image exceeds the {max_bytes} byte limit ({path.stat().st_size} bytes)")
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def encode_video_to_base64(video_path: str, max_bytes: Optional[int] = None) -> str:
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise ValueError(f"Video exceeds the {max_bytes} byte limit ({path.stat().st_size} bytes)")
    with open(video_path, "rb") as video_file:
        return base64.b64encode(video_file.read()).decode("utf-8")


def usage_footer(*responses) -> str:
    prompt_tokens = completion_tokens = reasoning_tokens = 0
    cost = 0.0
    has_cost = False
    for response in responses:
        if not response or not hasattr(response, 'usage'):
            continue
        usage = response.usage
        if usage:
            prompt_tokens += getattr(usage, 'prompt_tokens', 0)
            completion_tokens += getattr(usage, 'completion_tokens', 0)
            reasoning_tokens += getattr(usage, 'reasoning_tokens', 0)
        if hasattr(response, 'cost_usd') and response.cost_usd is not None:
            cost += response.cost_usd
            has_cost = True

    parts = []
    if prompt_tokens or completion_tokens:
        tokens = f"**Tokens:** {prompt_tokens:,} in / {completion_tokens:,} out"
        if reasoning_tokens:
            tokens += f" ({reasoning_tokens:,} reasoning)"
        parts.append(tokens)
    if has_cost:
        parts.append(f"**Cost:** ${cost:.4f}")
    if not parts:
        return ""
    return "\n\n---\n" + " · ".join(parts)


def extract_cost_from_output(content: str) -> float:
    """Read the standard usage footer cost from nested local tool output."""
    try:
        import re
        return sum(float(value) for value in re.findall(r"\*\*Cost:\*\*\s*\$([0-9]+(?:\.[0-9]+)?)", content))
    except Exception:
        return 0.0


async def load_history(session: str, store_param: Optional[Any] = None) -> list:
    session = scoped_session(session)
    active_store = store_param if store_param is not None else store
    try:
        return await active_store.load_messages(session)
    except Exception as e:
        logging.getLogger("GrokMCP").warning(f"Failed to load SQLite chat history for session '{session}': {e}")
    return []


async def save_history(session: str, history: list, store_param: Optional[Any] = None):
    session = scoped_session(session)
    active_store = store_param if store_param is not None else store
    try:
        # Single-transaction replace — the old delete-then-reinsert flow could
        # drop the whole history if a mid-loop write failed.
        await active_store.replace_messages(session, history)
    except Exception as e:
        logging.getLogger("GrokMCP").warning(f"Failed to save SQLite chat history for session '{session}': {e}")


async def append_and_save_history(session: str, history: list, prompt: str, reply: str, store_param: Optional[Any] = None, metadata: Optional[dict] = None):
    session = scoped_session(session)
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    msg_user = {"role": "user", "content": prompt, "time": now_str}
    msg_assistant = {"role": "assistant", "content": reply, "time": now_str}
    if metadata and isinstance(metadata.get("tool_trace"), list):
        # Persisted tool traces are capped so a busy agent turn cannot bloat
        # the messages metadata column.
        metadata = dict(metadata)
        metadata["tool_trace"] = metadata["tool_trace"][:20]
    if metadata:
        msg_assistant["metadata"] = metadata
    history.append(msg_user)
    history.append(msg_assistant)

    active_store = store_param if store_param is not None else store
    try:
        await active_store.save_message(session, "user", prompt)
        await active_store.save_message(session, "assistant", reply, metadata=metadata)
    except Exception as e:
        logging.getLogger("GrokMCP").warning(f"Failed to append/save SQLite chat history for session '{session}': {e}")

    # Auto-distillation (UNIGROK_AUTO_DISTILL=1, OFF by default): once a
    # session's history crosses the message threshold, submit ONE background
    # distill job for it per process — the job summarizes the stored history
    # into durable knowledge facts on the cheap coding model (src/jobs.py).
    if (
        session
        and _auto_distill_enabled()
        and len(history) >= _auto_distill_min_messages()
        and session not in _AUTO_DISTILLED_SESSIONS
    ):
        _AUTO_DISTILLED_SESSIONS.add(session)
        try:
            from .jobs import get_job_manager

            submitted = await get_job_manager().submit_distill(session)
            logging.getLogger("GrokMCP").info(
                f"Auto-distillation submitted for session '{session}': {submitted.get('job_id')}"
            )
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(
                f"Auto-distillation submit failed for session '{session}': {exc}"
            )


# ─── Local Context Compaction ────────────────────────────────────────────────
def _compact_threshold_tokens() -> int:
    """History size (estimated tokens, len/4) that triggers compaction."""
    try:
        return max(1000, int(os.environ.get("UNIGROK_COMPACT_THRESHOLD_TOKENS", "24000")))
    except ValueError:
        return 24000


def _estimate_history_tokens(history: List[dict]) -> int:
    """Cheap len/4 token estimate over a session's message contents."""
    return sum(len(str(msg.get("content") or "")) for msg in history) // 4


async def maybe_compact_history(
    session: str,
    history: List[dict],
    store_param: Optional[Any] = None,
    force: bool = False,
) -> List[dict]:
    """Compact a session's local history once it exceeds the token budget.

    LOCAL compaction by design: the installed SDK's compaction surface
    (Chat.compact() and client.chat.compact_context()) returns an OPAQUE
    ``encrypted_content`` blob meant to be re-sent as an assistant message —
    it cannot serve as the readable durable record this store keeps. Instead
    the oldest half of the history is summarized with ONE cheap coding-model
    call and replaced by a single system-role summary entry; the newest half
    stays verbatim. The replay paths (AgentLoop._init_chat, _call_plane)
    append system-role history entries so the summary reaches the model.

    Never compacts under UNI_GROK_TESTING unless force=True (hermetic tests
    exercise it with a mocked client). Returns the possibly-compacted history;
    every failure path returns the input unchanged.
    """
    if os.environ.get("UNI_GROK_TESTING") == "1" and not force:
        return history
    if not session or not history or len(history) < 4:
        return history
    if _estimate_history_tokens(history) < _compact_threshold_tokens():
        return history

    logger = logging.getLogger("GrokMCP")
    split = len(history) // 2
    oldest, newest = history[:split], history[split:]
    transcript = "\n".join(
        f"{msg.get('role')}: {str(msg.get('content') or '')[:4000]}" for msg in oldest
    )
    model = await resolve_model("coding")

    def _summarize():
        from xai_sdk.chat import system, user

        client = get_xai_client()
        # Dedicated TOOL-FREE chat: one cheap summarization call, no tools.
        chat = client.chat.create(model=model)
        chat.append(system(
            "Compress this conversation history into a dense factual summary. "
            "Keep decisions, constraints, file paths, numbers, and unresolved "
            "questions. Reply with the summary only."
        ))
        chat.append(user(transcript))
        return chat.sample()

    # The summarization is a real paid model call: it rides the per-model
    # circuit breaker like every other upstream call (an open breaker skips
    # compaction gracefully) and its cost is recorded in telemetry below so
    # the /metrics aggregates never undercount actual spend.
    start_time = time.time()
    try:
        check_circuit_breaker(model)
    except Exception as exc:
        logger.warning(f"History compaction skipped for session '{session}': {exc}")
        return history
    try:
        response = await run_blocking(
            _summarize,
            timeout=_env_timeout("UNIGROK_COMPACT_TIMEOUT", 60.0),
        )
        summary = str(getattr(response, "content", "") or "").strip()
    except Exception as exc:
        record_xai_failure(model)
        logger.warning(f"History compaction failed for session '{session}': {exc}")
        return history
    record_xai_success(model)
    cost = float(getattr(response, "cost_usd", 0.0) or 0.0)
    try:
        active_store = store_param if store_param is not None else store
        if active_store is not None:
            await active_store.save_telemetry(
                "history-compaction", "API", 1, time.time() - start_time, cost
            )
    except Exception as telemetry_err:
        logger.warning(
            f"History compaction telemetry save failed for session '{session}': {telemetry_err}"
        )
    if not summary:
        return history

    summary_entry = {
        "role": "system",
        "content": (
            f"[Compacted summary of {len(oldest)} earlier messages in this session]\n"
            f"{summary}"
        ),
        "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }
    compacted = [summary_entry] + list(newest)
    await save_history(session, compacted, store_param)
    logger.info(
        f"Compacted session '{session}' history: {len(history)} -> {len(compacted)} messages."
    )
    return compacted


# SelfOptimizationScore removed — the in-memory vanity score was superseded by
# RoutingAdvisor (persisted telemetry). Its only readers were write-only
# record_routing() call sites and the opt-in signature footer line.

# In-Memory Cache for Git deltas
class GitContextCache:
    """Tiny bounded TTL cache. get_dynamic_context caches one entry per
    distinct prompt hash (each holding a multi-KB context string), so entries
    MUST be evicted: expired keys are dropped on read and pruned on every
    write, and max_entries caps live keys (oldest-first eviction) so a
    long-running server never accumulates one entry per unique prompt."""

    def __init__(self, ttl: float = 3.0, max_entries: int = 64):
        self.ttl = ttl
        self.max_entries = max(1, int(max_entries))
        self._cache: Dict[str, tuple] = {}

    def get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        val, ts = entry
        if time.time() - ts < self.ttl:
            return val
        # Expired entries are dead weight — drop on sight.
        self._cache.pop(key, None)
        return None

    def set(self, key: str, val: Any):
        now = time.time()
        for stale in [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]:
            self._cache.pop(stale, None)
        while len(self._cache) >= self.max_entries:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            self._cache.pop(oldest, None)
        self._cache[key] = (val, now)

    def clear(self, key: Optional[str] = None):
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    def clear_prefix(self, prefix: str):
        """Drop every entry whose key starts with prefix — the prompt-keyed
        'dynamic_context:<hash>' family invalidates as one unit."""
        for k in [k for k in self._cache if k.startswith(prefix)]:
            self._cache.pop(k, None)

# Recursive MetaLayer
ModelPlane = Literal["reasoning", "composer", "cli-fallback"]

@dataclass
class MetaLayer:
    plan: str = ""
    reasoning: str = ""
    generation: str = ""
    reflection: str = ""
    plane: str = "API"
    route: str = ""
    model: str = ""
    profile: str = ""
    policy_mode: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    latency: float = 0.0
    fallback_occurred: bool = False
    degraded: bool = False
    routing_why: str = "auto"
    context_id: Optional[str] = None
    # Honest terminal outcome: final_answer | depth_exhausted | budget_exhausted
    # | fallback | error. Set at every terminal point; "unknown" means the
    # producing path never labeled the run.
    finish_reason: str = "unknown"
    # Structured tool observations from the agent run — persisted in message
    # metadata so a later turn can replay them and continue multi-step work.
    # Entries: {"tool_name", "tool_call_id", "success", "content" (≤2000 chars)}.
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    # xAI response id of the turn's final stored completion. Set only when the
    # run used server-side conversation state (store_messages=True); persisted
    # in sessions.api_thread_id so the next turn can continue the server-side
    # thread via previous_response_id instead of replaying local history.
    response_id: str = ""
    # True when the run self-escalated from the coding model to the planning
    # model via the escalate_reasoning internal tool (one-way, once per run).
    # Persisted in message metadata and task-memory metadata so future
    # retrieval sees which tasks needed escalation.
    escalated: bool = False
    # Source citation URLs surfaced by the run (deduped, in first-seen order).
    # Collected from Response.citations plus Response.inline_citations across
    # every sample of an AgentLoop run; mode="research" additionally requests
    # include=["inline_citations"] so upstream emits positional citations.
    citations: List[str] = field(default_factory=list)
    # The ROUTED model slug the run started on (same value task memory records
    # as its model column — escalated runs keep the original routed slug with
    # escalated=True marking the upgrade). Set by orchestrate at every
    # terminal point; consumed by the evals harness and structural graders.
    model: str = ""
    # Correlation id for this run (see the request-id section near the top of
    # this module): stamped by the _with_request_id decorator on
    # orchestrate/run_agent_turn, echoed as X-Request-Id by the gateway, and
    # written into telemetry metadata, job rows, and every log line.
    request_id: str = ""


def format_tool_trace_block(trace: List[Any], max_entries: int = 20, max_chars_per_entry: int = 600) -> str:
    """Render a persisted tool trace as a compact context block for replay.

    The SDK's assistant() helper cannot carry tool_calls, so replaying raw
    tool_result messages would orphan their ids — this text block is the
    replay format instead.
    """
    lines = ["[Tool observations from an earlier turn in this session]"]
    count = 0
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        if count >= max_entries:
            break
        name = str(entry.get("tool_name") or "unknown")
        status = "ok" if entry.get("success") else "error"
        content = str(entry.get("content") or "").strip()
        if len(content) > max_chars_per_entry:
            content = content[:max_chars_per_entry] + " [...truncated]"
        lines.append(f"- {name} ({status}): {content}")
        count += 1
    if count == 0:
        return ""
    return "\n".join(lines)


def _extract_citation_urls(response: Any) -> List[str]:
    """Best-effort citation URL extraction from an xAI Response.

    Sources (introspected field names on xai_sdk 1.17): Response.citations is
    a plain URL sequence; Response.inline_citations carries structured
    InlineCitation protos whose web_citation/x_citation/collections_citation
    oneof each expose a url (populated only when the chat was created with
    include=["inline_citations"]). Extraction never raises — unexpected
    shapes yield an empty list.
    """
    urls: List[str] = []

    def _add(value: Any):
        text = str(value or "").strip()
        if text and text not in urls:
            urls.append(text)

    try:
        for cited in (getattr(response, "citations", None) or []):
            if isinstance(cited, str):
                _add(cited)
    except Exception:
        pass
    try:
        for inline in (getattr(response, "inline_citations", None) or []):
            for source in ("web_citation", "x_citation", "collections_citation"):
                _add(getattr(getattr(inline, source, None), "url", None))
    except Exception:
        pass
    return urls


# ─────────────────────────────────────────────────────────────────────────────
# v2 AGENTIC LOOP INFRASTRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolObservation:
    """Structured result from an internal tool dispatch call."""
    tool_name: str
    success: bool
    content: str           # Observation text (already truncated)
    metadata: dict = field(default_factory=dict)
    elapsed: float = 0.0
    tool_call_id: Optional[str] = None


# Known-limit fallback directory used when the models API is unreachable.
_MODEL_TOKEN_LIMIT_FALLBACKS = {
    "grok-4.3": 131072,
    "grok-4.20-0309-reasoning": 131072,
    "grok-4.20-multi-agent": 131072,
    "grok-composer-2.5-fast": 131072,
    "grok-build": 131072,
    "grok-build-0.1": 131072,
}
_MODEL_MAX_TOKENS_TTL_SEC = 900.0  # 15 min — model limits change rarely
_MODEL_MAX_TOKENS_CACHE: Dict[str, tuple] = {}  # model → (value, timestamp)


def model_max_tokens_fallback(model_name: str) -> int:
    """Static known-limit lookup — never touches the network."""
    return _MODEL_TOKEN_LIMIT_FALLBACKS.get(model_name, 131072)


def get_model_max_tokens(model_name: str) -> int:
    """Resolve maximum prompt token lengths using the xAI SDK's models API,
    with a robust known fallback directory for CLI models and network isolation.

    Successful API lookups are cached per model for _MODEL_MAX_TOKENS_TTL_SEC so
    repeated agent runs do not pay a synchronous SDK network call each time.
    """
    cached = _MODEL_MAX_TOKENS_CACHE.get(model_name)
    if cached is not None:
        value, ts = cached
        if time.time() - ts < _MODEL_MAX_TOKENS_TTL_SEC:
            return value
    try:
        client = get_xai_client()
        model_info = client.models.get_language_model(model_name)
        if (hasattr(model_info, "max_prompt_length") and
            model_info.max_prompt_length and
            isinstance(model_info.max_prompt_length, int)):
            _MODEL_MAX_TOKENS_CACHE[model_name] = (model_info.max_prompt_length, time.time())
            return model_info.max_prompt_length
    except Exception:
        pass
    return model_max_tokens_fallback(model_name)


git_cache = GitContextCache()

def _knowledge_top_k() -> int:
    """UNIGROK_KNOWLEDGE_TOP_K (default 3, 0 disables, capped at 10):
    how many knowledge facts get injected into the workspace context."""
    try:
        return max(0, min(int(os.getenv("UNIGROK_KNOWLEDGE_TOP_K", "3")), 10))
    except ValueError:
        return 3


def format_knowledge_notes(facts: List[Dict[str, Any]]) -> str:
    """Render injected knowledge facts (mirrors format_task_memory_notes):
    clearly marked as recalled memory — a hint to verify, never proof."""
    if not facts:
        return ""
    lines = [
        "# Workspace Knowledge",
        "[Workspace knowledge] Distilled facts recalled from prior sessions — "
        "treat them as hints to verify against the live workspace, not proof.",
    ]
    for item in facts:
        fact = str(item.get("fact") or "").replace("\n", " ").strip()
        if not fact:
            continue
        lines.append(f"- {fact[:300]}")
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _rank_candidate_files(
    prompt_terms: List[str],
    candidates: List[Path],
    project_root: Path,
    max_candidates: int = 12,
) -> Path:
    """Pick the context file whose path + head best matches the prompt.

    Scores each candidate by _task_terms overlap between the prompt and the
    file's relative path plus its first ~2KB. Ties keep the incoming
    priority order (git-modified > last-commit > recently-touched), and zero
    overlap everywhere falls back to the first candidate — the pre-rework
    behavior of injecting the first modified file.
    """
    term_set = set(prompt_terms)
    best = candidates[0]
    best_score = 0
    for path in candidates[:max_candidates]:
        head = ""
        try:
            with open(path, "r", errors="ignore") as fh:
                head = fh.read(2048)
        except Exception:
            pass
        try:
            rel = str(path.relative_to(project_root))
        except ValueError:
            rel = str(path)
        score = len(term_set & set(_task_terms(f"{rel} {head}")))
        if score > best_score:
            best, best_score = path, score
    return best


async def get_dynamic_context(
    mcp_instance: Any = None, prompt: Optional[str] = None
) -> tuple[str, bool, str]:
    """Build the workspace system-prompt context: git state, the most
    relevant modified/recent file (ranked against `prompt` when given), and
    top-K knowledge facts matching the prompt. Cached per prompt-hash under
    the same short git-cache TTL as before; the promptless call keeps the
    exact legacy behavior (first modified file, no knowledge block)."""
    if not local_context_enabled():
        project_root = str(PathResolver.get_project_root())
        context = (
            "System Prompt - UniGrok xAI-only gateway\n"
            "You are Grok running behind the UniGrok single-agent API. "
            "Local workspace and git context are disabled for this runtime.\n"
            f"Project Folder: {project_root}\n"
        )
        context_hash = hashlib.sha256(context.encode("utf-8", errors="ignore")).hexdigest()[:10]
        context_id = f"ctx-cloudrun-nofile-{context_hash}"
        return context, False, context_id

    # Prompt-aware entries cache under their own key so different prompts
    # never collide; each entry rides the same short GitContextCache TTL.
    prompt_terms = _task_terms(prompt) if prompt else []
    cache_key = (
        f"dynamic_context:{_task_hash(prompt)}" if prompt_terms else "dynamic_context"
    )
    cached = git_cache.get(cache_key)
    if cached is not None:
        return cached

    project_root = str(PathResolver.get_project_root())
    recent_file = None
    recent_code = ""
    context_injected = False
    git_sha = "nogit"
    git_branch = "nobranch"
    candidate_paths: List[Path] = []

    try:
        proc_sha = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--short=12", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root
        )
        stdout_sha, _ = await communicate_with_timeout(proc_sha, 3.0)
        if proc_sha.returncode == 0:
            git_sha = stdout_sha.decode("utf-8", errors="ignore").strip() or git_sha
    except Exception:
        pass

    try:
        proc_branch = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--abbrev-ref", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root
        )
        stdout_branch, _ = await communicate_with_timeout(proc_branch, 3.0)
        if proc_branch.returncode == 0:
            branch_raw = stdout_branch.decode("utf-8", errors="ignore").strip()
            if branch_raw:
                git_branch = re.sub(r"[^A-Za-z0-9._-]+", "-", branch_raw)[:48]
    except Exception:
        pass

    # 1. Try Git mode first (extremely fast and high signal)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root
        )
        stdout, _ = await communicate_with_timeout(proc, 3.0)

        modified_files = []
        if proc.returncode == 0:
            for line in stdout.decode("utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    status, rel_path = parts
                    # Renames report "old -> new"; the file that exists is the
                    # post-arrow path.
                    if " -> " in rel_path:
                        rel_path = rel_path.split(" -> ", 1)[1]
                    rel_path = rel_path.strip('"')
                    if rel_path.endswith(('.py', '.js', '.ts', '.tsx', '.json', '.html', '.css', '.md', '.sh')):
                        modified_files.append(rel_path)

        # ALL matching modified files become ranking candidates (the old
        # code injected modified_files[0] unconditionally — the known
        # weakness this ranking fixes).
        for rel_path in modified_files:
            target_path = Path(project_root) / rel_path
            if target_path.exists():
                candidate_paths.append(target_path)
        if not candidate_paths:
            # Fallback: files from the last commit in the branch
            proc_log = await asyncio.create_subprocess_exec(
                "git", "log", "-n", "1", "--name-only", "--oneline",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_root
            )
            stdout_log, _ = await communicate_with_timeout(proc_log, 3.0)
            if proc_log.returncode == 0:
                lines = stdout_log.decode("utf-8", errors="ignore").splitlines()
                if len(lines) > 1:
                    for line in lines[1:]:
                        line = line.strip()
                        if line.endswith(('.py', '.js', '.ts', '.tsx', '.json', '.html', '.css', '.md', '.sh')):
                            target_path = Path(project_root) / line
                            if target_path.exists():
                                candidate_paths.append(target_path)
    except Exception as e:
        logging.getLogger("GrokMCP").warning(f"Git dynamic context resolution unavailable: {e}")

    # 2. Shallow file index fallback (most recently touched first)
    if not candidate_paths:
        try:
            candidate_files = []
            root_path = Path(project_root)

            for p in root_path.iterdir():
                if p.is_file() and p.suffix.lower() in ['.py', '.js', '.ts', '.tsx', '.json', '.html', '.css', '.md', '.sh']:
                    candidate_files.append(p)
                elif p.is_dir() and not p.name.startswith('.') and p.name not in ['node_modules', '.venv', 'venv', 'env', 'chats', 'logs']:
                    try:
                        for sub_p in p.iterdir():
                            if sub_p.is_file() and sub_p.suffix.lower() in ['.py', '.js', '.ts', '.tsx', '.json', '.html', '.css', '.md', '.sh']:
                                candidate_files.append(sub_p)
                    except Exception:
                        pass

            def _mtime(path: Path) -> float:
                try:
                    return path.stat().st_mtime
                except Exception:
                    return 0.0

            candidate_paths = [
                p.resolve()
                for p in sorted(candidate_files[:20], key=_mtime, reverse=True)
            ]
        except Exception as e:
            logging.getLogger("GrokMCP").error(f"Fallback context resolution failed: {e}", exc_info=True)

    # Candidate selection: without a prompt the first candidate wins (the
    # legacy behavior); with prompt terms the candidates are ranked by
    # path+head term overlap and the best match is injected.
    if candidate_paths:
        recent_file = candidate_paths[0]
        if prompt_terms and len(candidate_paths) > 1:
            recent_file = _rank_candidate_files(
                prompt_terms, candidate_paths, Path(project_root)
            )
        context_injected = True

    if recent_file and recent_file.exists():
        try:
            with open(recent_file, 'r', errors='ignore') as f:
                recent_code = "".join(f.readlines()[:100])
        except Exception as e:
            logging.getLogger("GrokMCP").error(f"Failed to read file context: {e}", exc_info=True)

    context = (
        "# UniGrok Workspace Context\n"
        "You are Grok running through the UniGrok MCP server. Ground responses in "
        "the current workspace, current git state, available tools, and explicit user instructions.\n\n"
        "Do not include a manual signature, sign-off footer, or runtime statistics; "
        "the server appends standard metadata when configured.\n"
        f"Project Folder: {project_root}\n"
    )
    if recent_file:
        context += f"Current/Active File: {recent_file}\n"
        if recent_code:
            recent_code_truncated = recent_code[:4000]
            context += f"Selected/Recent Code Content:\n```\n{recent_code_truncated}\n```\n"

    if mcp_instance:
        subagents_manifest = await get_tools_manifest(mcp_instance)
        if subagents_manifest:
            context += f"\n\n{subagents_manifest}"

    context_hash = hashlib.sha256(context.encode("utf-8", errors="ignore")).hexdigest()[:10]
    file_basis = f"{recent_file or 'nofile'}:{len(recent_code)}:{recent_code[:2048]}"
    file_hash = hashlib.sha256(file_basis.encode("utf-8", errors="ignore")).hexdigest()[:8]
    # Stable partition key: built ONLY from workspace state (branch, sha, file,
    # context) so task-memory retrieval can match across requests. A timestamp
    # here would make the context-match bonus unreachable. Computed BEFORE the
    # knowledge block below so recalled memory never perturbs the key.
    context_id = f"ctx-{git_branch}-{git_sha}-{file_hash}-{context_hash}"
    logging.getLogger("GrokMCP").info(f"Loaded versioned context snapshot: {context_id}")

    # 3. Knowledge memory: top-K global facts matching the prompt terms,
    # clearly marked as hints. Injected facts get touched (uses/last_used_at)
    # so retrieval telemetry accrues; any failure skips the block silently.
    top_k = _knowledge_top_k()
    if prompt_terms and top_k > 0:
        try:
            facts = await store.search_facts(prompt, scope="global", limit=top_k)
            selected = [f for f in facts if str(f.get("fact") or "").strip()][:top_k]
            notes = format_knowledge_notes(selected)
            if notes:
                context += f"\n\n{notes}"
                await store.touch_facts(
                    [f["id"] for f in selected if f.get("id") is not None]
                )
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(f"Knowledge context injection failed: {exc}")

    res = (context, context_injected, context_id)
    git_cache.set(cache_key, res)
    return res


DEFAULT_MAX_CONTEXT_LIMIT = 524288


@dataclass
class AgentLoopPolicy:
    """Configurable guardrails for the AgentLoop."""
    max_depth: int = 8                      # Max ReAct iterations
    max_tool_calls_per_turn: int = 6        # Cap parallel tool calls per turn
    per_tool_timeout_sec: float = 30.0      # Timeout per individual tool
    global_budget_usd: float = 0.50         # Hard cost ceiling for the full loop
    max_obs_chars: int = 8000               # Legacy character limit fallback
    max_obs_tokens: int = 2000              # Precise token-limit per tool observation
    enable_parallel_dispatch: bool = True   # asyncio.gather for parallel tool calls

    def __post_init__(self):
        # Synchronize token limits
        if self.max_obs_chars != 8000 and self.max_obs_tokens == 2000:
            self.max_obs_tokens = self.max_obs_chars // 4
        elif self.max_obs_tokens != 2000 and self.max_obs_chars == 8000:
            self.max_obs_chars = self.max_obs_tokens * 4


# ─── Agent Progress Events ───────────────────────────────────────────────────
# on_event callbacks receive small dicts describing run progress:
#   {"type": "depth", "depth", "max_depth", "cost_usd"}
#   {"type": "tool_start", "tool", "cost_usd"}
#   {"type": "tool_end", "tool", "success", "elapsed", "cost_usd"}
#   {"type": "content_delta", "text"}          (fast-plane real streaming)
# The callback may be sync or async; delivery failures are logged and NEVER
# break the run.

async def _emit_agent_event(on_event: Optional[Callable], event: Dict[str, Any]):
    if on_event is None:
        return
    try:
        result = on_event(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logging.getLogger("GrokMCP").warning(f"Agent progress event delivery failed: {exc}")


# ─── Internal Tool Registry ───────────────────────────────────────────────────
# Maps tool name → raw async callable (NO GrokInvocationContext/MCP wrappers).
# Registered from server.py after raw_* functions are defined.
_INTERNAL_TOOL_REGISTRY: Dict[str, Callable] = {}
_MUTATING_INTERNAL_TOOLS = {
    "git_apply_patch",
    "git_commit",
    "git_create_branch",
}
_INTERNAL_TOOLS_BOOTSTRAPPED = False


def register_internal_tool(name: str, fn: Callable):
    """Register a raw async callable for internal agent dispatch."""
    _INTERNAL_TOOL_REGISTRY[name] = fn


def ensure_internal_tools_registered():
    """Import modular tools for side-effect registration when utils is used directly."""
    global _INTERNAL_TOOLS_BOOTSTRAPPED
    if _INTERNAL_TOOLS_BOOTSTRAPPED:
        return
    try:
        from .tools import chats as _chats  # noqa: F401
        from .tools import faq as _faq  # noqa: F401
        from .tools import git as _git  # noqa: F401
        from .tools import media as _media  # noqa: F401
        from .tools import system as _system  # noqa: F401
        _INTERNAL_TOOLS_BOOTSTRAPPED = True
    except Exception as exc:
        logging.getLogger("GrokMCP").warning(f"Internal tool lazy registration failed: {exc}")


async def dispatch_internal_tool(
    name: str,
    arguments: Dict[str, Any],
    timeout_sec: float = 30.0,
) -> ToolObservation:
    """Execute a registered raw tool with timeout and full error isolation."""
    if name not in _INTERNAL_TOOL_REGISTRY:
        ensure_internal_tools_registered()
    if name not in _INTERNAL_TOOL_REGISTRY:
        return ToolObservation(
            tool_name=name, success=False,
            content=f"Tool '{name}' not found in internal registry."
        )
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            _INTERNAL_TOOL_REGISTRY[name](**arguments),
            timeout=timeout_sec
        )
        content = bound_tool_output(str(result))
        if name in _MUTATING_INTERNAL_TOOLS:
            # Prefix clear: the workspace changed, so BOTH the legacy
            # 'dynamic_context' key and every prompt-keyed
            # 'dynamic_context:<hash>' entry are stale.
            git_cache.clear_prefix("dynamic_context")
            _TOOLS_MANIFEST_CACHE.clear("tools_manifest")
        tool_cost = extract_cost_from_output(content)
        metadata = {"cost_usd": tool_cost} if tool_cost else {}
        return ToolObservation(
            tool_name=name, success=True,
            content=content, metadata=metadata, elapsed=time.time() - t0
        )
    except asyncio.TimeoutError:
        return ToolObservation(
            tool_name=name, success=False,
            content=f"Tool '{name}' timed out after {timeout_sec}s.",
            elapsed=time.time() - t0
        )
    except Exception as e:
        return ToolObservation(
            tool_name=name, success=False,
            content=bound_tool_output(f"Tool '{name}' error - {type(e).__name__}: {e}"),
            elapsed=time.time() - t0
        )


# ─── Self-Escalation Internal Tool ────────────────────────────────────────────
# The model can hand a run off to the stronger planning model mid-loop. The
# AgentLoop intercepts this tool BEFORE registry dispatch (_dispatch_one) and
# rebuilds its chat on the planning alias before the next sample. Escalation
# is one-way, once per run, and only offered when the loop started on the
# coding model.
_ESCALATE_TOOL_NAME = "escalate_reasoning"
_ESCALATE_TOOL_DESCRIPTION = (
    "Call when the current task needs deeper reasoning than you can provide — "
    "hands the conversation to the stronger planning model."
)


async def _escalate_reasoning_fallback(reason: str = "") -> str:
    """Registry fallback: escalation only has meaning inside an AgentLoop run
    (the loop intercepts the call before registry dispatch ever runs)."""
    return "escalation is only available inside an active agent run."


register_internal_tool(_ESCALATE_TOOL_NAME, _escalate_reasoning_fallback)


# ─── xAI Built-in Tool Schemas for the AgentLoop ─────────────────────────────
# Tier 1: xAI server-side tools — executed inside xAI infrastructure.
# These are passed directly to chat.sample(tools=...) and avoid re-entrancy.
def _build_agentic_tools_schema():
    """Build the tool schema list for AgentLoop. Uses xAI built-in helpers."""
    try:
        from xai_sdk.tools import (
            web_search as _xai_web_search,
            x_search as _xai_x_search,
            code_execution as _xai_code_execution,
        )
        return [
            _xai_code_execution(),   # Python sandbox — runs in xAI infra
            _xai_web_search(),       # Real-time web — runs in xAI infra
            _xai_x_search(),         # X/Twitter data — runs in xAI infra
        ]
    except Exception as e:
        logging.getLogger("GrokMCP").warning(f"Could not build agentic tools schema: {e}")
        return []

AGENTIC_TOOLS_SCHEMA = _build_agentic_tools_schema()


# ─── AgentLoop ────────────────────────────────────────────────────────────────
class AgentLoop:
    """
    True ReAct agentic loop with parallel tool dispatch, cost/timeout guardrails,
    and observation truncation. Replaces the closed text-echo recursive loop.

    Architecture:
      Tier 1 (xAI server-side built-ins): code_execution, web_search, x_search
        → Passed in AGENTIC_TOOLS_SCHEMA, run inside xAI infra, zero re-entrancy risk.
      Tier 2 (local raw callables): generate_image, file ops, filesystem reads
        → Dispatched via _INTERNAL_TOOL_REGISTRY, executed locally.
    """

    def __init__(
        self,
        policy: AgentLoopPolicy,
        dynamic_sys_prompt: str,
        model: str,
        store: Any = None,
        agent_count: Optional[int] = None,
        profile: Optional[Dict[str, Any]] = None,
        on_event: Optional[Callable] = None,
        include: Optional[List[str]] = None,
    ):
        # Copy the policy — run() adjusts observation limits per model and must
        # never mutate a caller-shared AgentLoopPolicy instance.
        self.policy = replace(policy)
        self.sys_prompt = dynamic_sys_prompt
        self.model = model
        self.store = store
        self.agent_count = agent_count
        # Extra response surfaces to request (e.g. ["inline_citations"] for
        # mode="research"); forwarded to chat.create behind a capability gate.
        self.include = list(include) if include else None
        self.profile = profile or load_grok_profile(model)
        # Optional sync/async progress callback (see _emit_agent_event).
        self.on_event = on_event
        # Cost-so-far snapshot for progress events, updated as run() accrues.
        self._cost_so_far = 0.0
        # Self-escalation state: one-way, once per run, and only offered when
        # the loop STARTS on the coding model (see run()).
        self._escalation_available = False
        self._escalation_pending = False
        self._escalated = False
        self._escalation_target: Optional[str] = None
        self._logger = logging.getLogger("GrokMCP.AgentLoop")

    async def run(
        self,
        prompt: str,
        session: Optional[str] = None,
        history: Optional[List[dict]] = None,
        input_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> MetaLayer:
        """Execute the full ReAct loop and return a populated MetaLayer."""
        from xai_sdk.chat import user, system, assistant, tool_result as sdk_tool_result

        # Routing aliases ('planning'/'coding'/'vision') resolve here so a
        # caller may hand them to the loop directly; explicit slugs pass
        # through resolve_model unchanged.
        self.model = await resolve_model(self.model)

        # Dynamically scale policy limits based on the model's capabilities.
        # get_model_max_tokens can hit the network on a cache miss, so keep it
        # off the event loop and fall back to the known limits on timeout/error.
        try:
            max_tokens = await run_blocking(
                get_model_max_tokens,
                self.model,
                timeout=_env_timeout("UNIGROK_MODEL_INFO_TIMEOUT", 5.0),
            )
        except Exception as _limits_err:
            max_tokens = model_max_tokens_fallback(self.model)
            self._logger.warning(
                f"Model capacity lookup failed for {self.model}; "
                f"using fallback {max_tokens} tokens: {_limits_err}"
            )
        self.policy.max_obs_tokens = min(8000, max_tokens // 16)
        self.policy.max_obs_chars = self.policy.max_obs_tokens * 4
        self._logger.info(f"Dynamic capacity adjusted for model {self.model}: {max_tokens} tokens (max {self.policy.max_obs_tokens} tokens per tool call)")

        # Self-escalation eligibility: only offered when the loop STARTS on
        # the coding model and the planning alias resolves to a genuinely
        # different slug. One-way and once per run (see _handle_escalation).
        coding_slug = await resolve_model("coding")
        planning_slug = await resolve_model("planning")
        self._escalation_available = (
            self.model == coding_slug and planning_slug != self.model
        )
        self._escalation_target = planning_slug if self._escalation_available else None
        self._escalation_pending = False
        self._escalated = False

        layer = MetaLayer()
        all_observations: List[ToolObservation] = []
        all_reasoning: List[str] = []
        all_citations: List[str] = []
        total_cost = 0.0
        total_tokens = 0
        last_response_id: Optional[str] = None
        response = None
        ensure_internal_tools_registered()
        custom_tools = _build_custom_tools(include_escalation=self._escalation_available)
        tools = (AGENTIC_TOOLS_SCHEMA or []) + custom_tools
        if not tools:
            tools = None

        # ── Server-side conversation state ────────────────────────────────────
        # With an active session and SDK support, this turn's messages are
        # stored server-side (store_messages=True) and the NEXT turn continues
        # the thread via previous_response_id instead of replaying local
        # history — the SQLite record stays the durable source of truth, only
        # the transport payload shrinks. input_messages turns keep full replay:
        # the caller controls that transcript. UNIGROK_SERVER_STATE=0 kills it.
        use_server_state = (
            session is not None
            and self.store is not None
            and input_messages is None
            and _server_state_enabled()
            and _server_state_supported()
        )
        previous_response_id: Optional[str] = None
        if use_server_state:
            try:
                session_data = await self.store.get_session(session)
            except Exception as _sess_err:
                session_data = None
                self._logger.warning(f"Server-state session lookup failed: {_sess_err}")
            saved_id = (session_data or {}).get("api_thread_id")
            # Legacy rows stored the session name itself as a placeholder —
            # that is not a response id and must never be sent upstream.
            if isinstance(saved_id, str) and saved_id and saved_id != str(session):
                previous_response_id = saved_id

        def _chat_params():
            # Shared by _init_chat and the escalation rebuild: reads the
            # CURRENT self.model/self.profile so a rebuilt chat rides the
            # planning model's settings.
            chat_params = {"model": self.model}
            if self.profile.get("temperature") is not None:
                chat_params["temperature"] = self.profile["temperature"]
            if self.profile.get("top_p") is not None:
                chat_params["top_p"] = self.profile["top_p"]
            if self.profile.get("reasoning_effort") and _chat_create_supports("reasoning_effort"):
                chat_params["reasoning_effort"] = self.profile["reasoning_effort"]
            if session and _chat_create_supports("conversation_id"):
                # Observability only: the SDK surfaces this as the
                # gen_ai.conversation.id span attribute so a session's calls
                # group in traces. It does NOT route or prompt-cache.
                chat_params["conversation_id"] = str(session)
            if use_server_state:
                chat_params["store_messages"] = True
                if previous_response_id:
                    chat_params["previous_response_id"] = previous_response_id
            if tools:
                chat_params["tools"] = tools
            if self.agent_count is not None:
                chat_params["agent_count"] = self.agent_count
            if self.include and _chat_create_supports("include"):
                chat_params["include"] = list(self.include)
            return chat_params

        def _init_chat():
            client = get_xai_client()
            chat = client.chat.create(**_chat_params())
            chat.append(system(self.sys_prompt))
            if input_messages:
                _append_sdk_messages(chat, input_messages, include_system=False)
            elif history and previous_response_id is None:
                # Full local replay — skipped when the server already holds
                # the thread (previous_response_id continues it upstream).
                for msg in history:
                    r = msg.get("role")
                    c = msg.get("content", "")
                    if r == "user":
                        chat.append(user(c))
                    elif r == "system":
                        # Compaction stores summaries as system-role entries.
                        chat.append(system(c))
                    elif r == "assistant":
                        # Replay persisted tool observations before the reply
                        # so multi-step work continues across turns. assistant()
                        # cannot carry tool_calls, so raw tool_result replay
                        # would orphan ids — a compact text block is used.
                        meta = msg.get("metadata")
                        trace = meta.get("tool_trace") if isinstance(meta, dict) else None
                        if trace:
                            trace_block = format_tool_trace_block(trace)
                            if trace_block:
                                chat.append(assistant(trace_block))
                        chat.append(assistant(c))
            if not input_messages:
                chat.append(user(prompt))
            return chat

        chat = await run_blocking(_init_chat, timeout=10.0)

        _MAX_SAMPLE_RETRIES = 2

        async def _sample_with_retries(stage: str):
            """chat.sample() with error-classified exponential-backoff retry.
            Fatal errors (auth/validation) raise immediately instead of burning
            retries; retryable errors honor a Retry-After hint when the
            exception exposes one. Every attempt reports to the per-model
            circuit breaker, which fails fast while open. Shared by the depth
            loop and the budget-stop final synthesis below."""
            def _sample():
                return chat.sample()

            for _attempt in range(_MAX_SAMPLE_RETRIES + 1):
                check_circuit_breaker(self.model)
                try:
                    sampled = await run_blocking(
                        _sample,
                        timeout=_env_timeout("UNIGROK_AGENT_SAMPLE_TIMEOUT", 180.0),
                    )
                    record_xai_success(self.model)
                    return sampled
                except Exception as _sample_err:
                    record_xai_failure(self.model)
                    if classify_xai_error(_sample_err) == "fatal":
                        self._logger.error(
                            f"chat.sample() fatal error at {stage}; not retrying: {_sample_err}"
                        )
                        raise
                    if _attempt == _MAX_SAMPLE_RETRIES:
                        self._logger.error(
                            f"chat.sample() failed after {_MAX_SAMPLE_RETRIES} retries "
                            f"at {stage}: {_sample_err}"
                        )
                        raise
                    _wait = _retry_after_hint(_sample_err) or 2 ** _attempt  # 1s then 2s
                    self._logger.warning(
                        f"chat.sample() failed (attempt {_attempt + 1}/"
                        f"{_MAX_SAMPLE_RETRIES + 1}), retrying in {_wait}s: {_sample_err}"
                    )
                    await asyncio.sleep(_wait)

        def _track_usage(sampled):
            nonlocal total_tokens, total_cost, last_response_id
            if sampled.usage:
                total_tokens += (
                    getattr(sampled.usage, "prompt_tokens", 0)
                    + getattr(sampled.usage, "completion_tokens", 0)
                )
            if hasattr(sampled, "cost_usd") and sampled.cost_usd:
                total_cost += sampled.cost_usd
            self._cost_so_far = total_cost
            # The LAST stored completion id is the thread head the next turn
            # continues from via previous_response_id.
            rid = getattr(sampled, "id", None)
            if isinstance(rid, str) and rid:
                last_response_id = rid
            # Citations accumulate across every sample (deduped, first-seen
            # order) so multi-step research runs keep all their sources.
            for cited_url in _extract_citation_urls(sampled):
                if cited_url not in all_citations:
                    all_citations.append(cited_url)

        def _inject_observations(observations):
            for obs in observations:
                truncated = self._truncate(obs.content, obs.tool_name)
                chat.append(sdk_tool_result(truncated, tool_call_id=obs.tool_call_id))

        def _rebuild_chat_for_escalation(old_chat):
            # The SDK chat's full conversation lives on chat.proto.messages
            # (exposed via the .messages property); each entry is a
            # chat_pb2.Message that append() re-appends verbatim, so the new
            # planning-model chat carries the entire existing conversation —
            # system prompt, tool calls, and tool results included.
            client = get_xai_client()
            new_chat = client.chat.create(**_chat_params())
            for msg in list(getattr(old_chat, "messages", []) or []):
                new_chat.append(msg)
            return new_chat

        async def _maybe_escalate():
            """Rebuild the chat on the planning model before the next sample
            once the model has called escalate_reasoning. Failures degrade
            gracefully: the run continues on the current model."""
            nonlocal chat
            if not self._escalation_pending or self._escalated:
                return
            old_model, old_profile = self.model, self.profile
            try:
                self.model = self._escalation_target
                self.profile = load_grok_profile(self.model)
                chat = await run_blocking(_rebuild_chat_for_escalation, chat, timeout=10.0)
                self._escalated = True
                layer.escalated = True
                self._logger.info(
                    f"Self-escalation: chat rebuilt on {self.model} (was {old_model})"
                )
            except Exception as esc_err:
                self.model, self.profile = old_model, old_profile
                self._logger.warning(
                    f"Self-escalation rebuild failed; continuing on {old_model}: {esc_err}"
                )
            finally:
                # One attempt only — never retry the rebuild every depth.
                self._escalation_pending = False

        try:
            for depth in range(self.policy.max_depth):
                self._logger.info(f"AgentLoop depth {depth + 1}/{self.policy.max_depth}")
                await _emit_agent_event(self.on_event, {
                    "type": "depth",
                    "depth": depth + 1,
                    "max_depth": self.policy.max_depth,
                    "cost_usd": total_cost,
                })

                # ── Self-escalation: swap to the planning model when the
                # model requested it via escalate_reasoning last turn ─────────
                await _maybe_escalate()

                # ── REASON: sample with exponential-backoff retry ──────────────
                response = await _sample_with_retries(f"depth {depth + 1}")

                # Track cost and tokens
                _track_usage(response)

                # Budget guardrail — hard stop
                if total_cost >= self.policy.global_budget_usd:
                    self._logger.warning(
                        f"AgentLoop budget ceiling hit at ${total_cost:.4f} "
                        f"(limit: ${self.policy.global_budget_usd:.2f})"
                    )
                    layer.generation = getattr(response, "content", "") or layer.generation
                    layer.finish_reason = "budget_exhausted"
                    break

                content = getattr(response, "content", "") or ""
                all_reasoning.append(content)

                # Must append assistant response to chat BEFORE tool_results
                def _append_response(chat=chat, response=response):
                    chat.append(response)
                await run_blocking(_append_response, timeout=5.0)

                tool_calls = getattr(response, "tool_calls", None) or []

                if not tool_calls:
                    # Model produced final answer — no more tools needed
                    layer.generation = content
                    layer.finish_reason = "final_answer"
                    self._logger.info(f"AgentLoop complete at depth {depth + 1} (no tool calls)")
                    break

                # Cap tool calls per turn
                dropped_calls = tool_calls[self.policy.max_tool_calls_per_turn :]
                tool_calls = tool_calls[: self.policy.max_tool_calls_per_turn]

                # ── EXECUTE: Parallel dispatch ─────────────────────────────────
                observations = await self._dispatch_parallel(tool_calls)
                # The full assistant response (with ALL tool_calls) was already
                # appended, so calls dropped by the cap must still receive a
                # tool_result or their orphaned ids poison the next sample.
                for tc in dropped_calls:
                    observations.append(
                        ToolObservation(
                            tool_name=self._extract_tool_name(tc),
                            success=False,
                            content=(
                                "skipped: per-turn tool-call cap "
                                f"({self.policy.max_tool_calls_per_turn}) reached"
                            ),
                            tool_call_id=self._extract_tool_call_id(tc),
                        )
                    )
                all_observations.extend(observations)
                local_tool_cost = sum(
                    float(obs.metadata.get("cost_usd", 0.0) or 0.0)
                    for obs in observations
                )
                if local_tool_cost:
                    total_cost += local_tool_cost
                    self._cost_so_far = total_cost
                    self._logger.info(
                        f"AgentLoop local tool cost ${local_tool_cost:.4f}; "
                        f"cumulative ${total_cost:.4f}"
                    )
                if total_cost >= self.policy.global_budget_usd:
                    self._logger.warning(
                        f"AgentLoop budget ceiling hit after local tool dispatch at ${total_cost:.4f} "
                        f"(limit: ${self.policy.global_budget_usd:.2f})"
                    )
                    layer.generation = (
                        content
                        or "Budget ceiling reached after local tool dispatch; stopping before the next model call."
                    )
                    layer.finish_reason = "budget_exhausted"
                    # Inject the tool results, then attempt ONE final sample so
                    # callers get a synthesized answer instead of the tool-calling
                    # preamble. Any tool_calls it requests are ignored; on failure
                    # the fallback text above stands.
                    try:
                        await run_blocking(_inject_observations, observations, timeout=5.0)
                        final_response = await _sample_with_retries(
                            f"budget synthesis (depth {depth + 1})"
                        )
                        _track_usage(final_response)
                        final_content = getattr(final_response, "content", "") or ""
                        if final_content:
                            all_reasoning.append(final_content)
                            layer.generation = final_content
                    except Exception as _final_err:
                        self._logger.warning(
                            f"Budget-stop final synthesis failed; keeping fallback text: {_final_err}"
                        )
                    break

                # ── OBSERVE: Inject tool results back into chat ────────────────
                await run_blocking(_inject_observations, observations, timeout=5.0)

            else:
                # Exhausted max_depth without a clean break
                layer.finish_reason = "depth_exhausted"
                if not layer.generation:
                    layer.generation = (
                        getattr(response, "content", "") if response else
                        "Max agent depth reached without a final answer."
                    )

        finally:
            pass

        layer.tokens = total_tokens
        layer.cost_usd = total_cost
        layer.citations = all_citations
        if use_server_state and last_response_id:
            layer.response_id = last_response_id
        layer.plane = "API"
        layer.route = "agentic"
        layer.profile = str(self.profile.get("profile") or "")
        layer.policy_mode = current_policy_mode()
        layer.reasoning = "\n\n---\n\n".join(all_reasoning)
        layer.reflection = "\n\n".join(
            f"**{obs.tool_name}** ({'✓' if obs.success else '✗'}, {obs.elapsed:.1f}s):\n"
            f"{obs.content[:400]}"
            for obs in all_observations
        ) if all_observations else "No tool observations."
        layer.tool_trace = [
            {
                "tool_name": obs.tool_name,
                "tool_call_id": obs.tool_call_id,
                "success": obs.success,
                "content": obs.content[:2000],
            }
            for obs in all_observations
        ]
        return layer

    async def _dispatch_parallel(self, tool_calls: list) -> List[ToolObservation]:
        """Execute tool calls respecting the enable_parallel_dispatch policy.

        Parallel (default): asyncio.gather fires all calls simultaneously.
        Serial: each call awaited in order — useful for debugging or tools
        that mutate shared state and must not overlap.
        """
        if self.policy.enable_parallel_dispatch:
            tasks = [self._dispatch_one(tc) for tc in tool_calls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            observations = []
            for tc, result in zip(tool_calls, results):
                if isinstance(result, ToolObservation):
                    observations.append(result)
                    continue
                tool_call_id = self._extract_tool_call_id(tc)
                observations.append(
                    ToolObservation(
                        tool_name=self._extract_tool_name(tc),
                        success=False,
                        content=bound_tool_output(
                            f"Tool dispatch error - {type(result).__name__}: {result}"
                        ),
                        tool_call_id=tool_call_id,
                    )
                )
            return observations
        else:
            results = []
            for tc in tool_calls:
                try:
                    results.append(await self._dispatch_one(tc))
                except Exception as exc:
                    results.append(
                        ToolObservation(
                            tool_name=self._extract_tool_name(tc),
                            success=False,
                            content=bound_tool_output(
                                f"Tool dispatch error - {type(exc).__name__}: {exc}"
                            ),
                            tool_call_id=self._extract_tool_call_id(tc),
                        )
                    )
            return results

    @staticmethod
    def _extract_tool_call_id(tc) -> Optional[str]:
        raw_tool_call_id = getattr(tc, "id", None)
        return raw_tool_call_id if isinstance(raw_tool_call_id, str) and raw_tool_call_id else None

    @staticmethod
    def _extract_tool_name(tc) -> str:
        try:
            return str(tc.function.name or "unknown")
        except Exception:
            return "unknown"

    async def _dispatch_one(self, tc) -> ToolObservation:
        """Dispatch a single tool call — Tier 2 local tools only.

        Note: Tier 1 xAI server-side tools (code_execution, web_search, x_search)
        are handled directly by the xAI SDK inside chat.sample() and do NOT
        surface as tool_calls here. Only custom/local tool calls reach this method.
        """
        tool_call_id = self._extract_tool_call_id(tc)
        try:
            name = tc.function.name
            raw_args = tc.function.arguments
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except Exception as e:
            return ToolObservation(
                tool_name="unknown", success=False,
                content=f"Failed to parse tool call: {e}",
                tool_call_id=tool_call_id,
            )

        # Override default parameter truncation settings to match dynamic capacity limit
        max_chars_fallback = self.policy.max_obs_tokens * 4
        if name == "read_local_file" and "max_chars" not in arguments:
            arguments["max_chars"] = max_chars_fallback
        elif name == "get_file_content" and "max_bytes" not in arguments:
            arguments["max_bytes"] = max_chars_fallback

        await _emit_agent_event(self.on_event, {
            "type": "tool_start",
            "tool": name,
            "cost_usd": self._cost_so_far,
        })
        if name == _ESCALATE_TOOL_NAME:
            # Loop-bound tool: handled here, never via the registry fallback.
            obs = self._handle_escalation(arguments, tool_call_id)
        else:
            obs = await dispatch_internal_tool(
                name, arguments, self.policy.per_tool_timeout_sec
            )
        obs.tool_call_id = tool_call_id
        await _emit_agent_event(self.on_event, {
            "type": "tool_end",
            "tool": name,
            "success": obs.success,
            "elapsed": obs.elapsed,
            "cost_usd": self._cost_so_far,
        })
        return obs

    def _handle_escalation(self, arguments: Dict[str, Any], tool_call_id: Optional[str]) -> ToolObservation:
        """In-loop handler for the escalate_reasoning tool.

        The first accepted call schedules a chat rebuild on the planning model
        before the NEXT sample (see _maybe_escalate in run()); escalation is
        one-way and once per run, so any later call — or a call from a run
        that did not start on the coding model — is a no-op observation.
        """
        if not self._escalation_available:
            return ToolObservation(
                tool_name=_ESCALATE_TOOL_NAME, success=False,
                content="escalation unavailable — this run did not start on the coding model.",
                tool_call_id=tool_call_id,
            )
        if self._escalated or self._escalation_pending:
            return ToolObservation(
                tool_name=_ESCALATE_TOOL_NAME, success=True,
                content="escalation already active — one escalation per run.",
                tool_call_id=tool_call_id,
            )
        reason = str(arguments.get("reason") or "").strip()
        self._escalation_pending = True
        self._logger.info(
            f"Self-escalation requested (reason: {reason or 'none given'})"
        )
        return ToolObservation(
            tool_name=_ESCALATE_TOOL_NAME, success=True,
            content=f"escalation accepted — continuing with {self._escalation_target}",
            tool_call_id=tool_call_id,
        )

    def _truncate(self, content: str, tool_name: str) -> str:
        """Truncate large tool outputs to prevent context explosion."""
        max_obs_tokens = getattr(self.policy, "max_obs_tokens", 2000)
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            tokens = enc.encode(content)
            if len(tokens) <= max_obs_tokens:
                return content
            truncated_content = enc.decode(tokens[:max_obs_tokens])
            return truncated_content + f"\n\n[...truncated from '{tool_name}' output to fit {max_obs_tokens} tokens]"
        except Exception:
            limit = max_obs_tokens * 4
            if len(content) <= limit:
                return content
            return (
                content[:limit]
                + f"\n\n[...{len(content) - limit} chars truncated from '{tool_name}' output]"
            )


# ─── Execution Dispatch Constants ────────────────────────────────────────────
DEFAULT_CODING_MODEL = "grok-build-0.1"
DEFAULT_PLANNING_MODEL = "grok-4.3"


# ─── Dynamic Model Resolution ────────────────────────────────────────────────
# Routing aliases resolved against the live xAI catalog so a retired default
# slug degrades to the closest live model instead of hard-failing every call.
_MODEL_ALIAS_ENV_OVERRIDES = {
    "planning": "UNIGROK_PLANNING_MODEL",
    "coding": "UNIGROK_CODING_MODEL",
    "vision": "UNIGROK_VISION_MODEL",
}


def _model_alias_default(alias: str) -> str:
    """Static default slug for a routing alias (vision rides the planning
    default — grok-4.3 is the vision-capable flagship)."""
    if alias == "coding":
        return DEFAULT_CODING_MODEL
    return DEFAULT_PLANNING_MODEL


class ModelResolver:
    """Resolve the routing aliases `planning` / `coding` / `vision` to slugs.

    Resolution order per alias:
      1. Env override (UNIGROK_PLANNING_MODEL / UNIGROK_CODING_MODEL /
         UNIGROK_VISION_MODEL) — wins over everything, including testing mode.
      2. The static default when discovery is disabled
         (UNIGROK_MODEL_DISCOVERY=0) or under UNI_GROK_TESTING (hermetic
         tests never discover).
      3. The TTL-cached live catalog (discover_xai_api_models): the configured
         default when it is still listed, else the closest available slug with
         a WARNING naming old → new.

    Resolution is lazy (first use), never runs at import, and never blocks the
    event loop — catalog discovery is bridged through run_blocking with its
    own timeout inside discover_xai_api_models. Non-alias inputs pass through
    unchanged so explicit model slugs keep working everywhere.
    """

    _TTL_SEC = 900.0  # 15 min — mirrors _MODEL_MAX_TOKENS_TTL_SEC

    def __init__(self, ttl: float = _TTL_SEC):
        self._ttl = ttl
        self._resolved: Dict[str, tuple] = {}  # alias → (slug, timestamp)
        self._lock = asyncio.Lock()

    def invalidate(self):
        self._resolved.clear()

    @staticmethod
    def _discovery_enabled() -> bool:
        if os.environ.get("UNI_GROK_TESTING") == "1":
            return False
        return os.environ.get("UNIGROK_MODEL_DISCOVERY", "1").strip().lower() not in ("0", "false", "no")

    @staticmethod
    def _version_key(model_id: str) -> tuple:
        """Sortable version tuple: 'grok-4.20-0309-reasoning' → (4, 20, 309)."""
        numbers = tuple(int(part) for part in re.findall(r"\d+", model_id))
        return numbers or (0,)

    @classmethod
    def _pick_closest(cls, alias: str, catalog_ids: List[str]) -> Optional[str]:
        """Closest live substitute for a retired default: coding prefers
        code/build slugs, vision prefers vision slugs, and everything falls
        back to the newest reasoning-capable grok slug, then any grok slug."""
        def newest(candidates: List[str]) -> Optional[str]:
            return max(candidates, key=cls._version_key) if candidates else None

        grok_ids = [mid for mid in catalog_ids if mid.startswith("grok")]
        if alias == "coding":
            picked = newest([mid for mid in grok_ids if "code" in mid or "build" in mid])
            if picked:
                return picked
        if alias == "vision":
            picked = newest([mid for mid in grok_ids if "vision" in mid])
            if picked:
                return picked
        return newest([mid for mid in grok_ids if "reasoning" in mid]) or newest(grok_ids)

    async def resolve(self, alias_or_model: str) -> str:
        alias = str(alias_or_model or "").strip()
        if alias not in _MODEL_ALIAS_ENV_OVERRIDES:
            return alias  # explicit slug — pass through unchanged
        override = os.environ.get(_MODEL_ALIAS_ENV_OVERRIDES[alias], "").strip()
        if override:
            return override
        default = _model_alias_default(alias)
        if not self._discovery_enabled():
            return default
        cached = self._resolved.get(alias)
        if cached is not None and time.time() - cached[1] < self._ttl:
            return cached[0]
        async with self._lock:
            cached = self._resolved.get(alias)
            if cached is not None and time.time() - cached[1] < self._ttl:
                return cached[0]
            resolved = await self._resolve_from_catalog(alias, default)
            # Discovery-down results are cached too: the fallback IS the
            # default, and re-probing every call would stall each turn on the
            # discovery timeout while the API is unreachable.
            self._resolved[alias] = (resolved, time.time())
            return resolved

    async def _resolve_from_catalog(self, alias: str, default: str) -> str:
        try:
            discovery = await discover_xai_api_models()
        except Exception as exc:  # discover catches internally; belt and braces
            logging.getLogger("GrokMCP").warning(
                f"Model alias '{alias}': catalog discovery raised, using default '{default}': {exc}"
            )
            return default
        if not discovery.get("available"):
            return default
        catalog_ids = [entry.get("id") for entry in discovery.get("models", []) if entry.get("id")]
        if default in catalog_ids:
            return default
        picked = self._pick_closest(alias, catalog_ids)
        if picked:
            logging.getLogger("GrokMCP").warning(
                f"Model alias '{alias}': configured default '{default}' is absent "
                f"from the live catalog; resolved '{default}' -> '{picked}'."
            )
            return picked
        return default


_MODEL_RESOLVER = ModelResolver()


async def resolve_model(alias_or_model: str) -> str:
    """Resolve a routing alias (`planning`/`coding`/`vision`) to a live model
    slug via the shared ModelResolver; explicit slugs pass through unchanged."""
    return await _MODEL_RESOLVER.resolve(alias_or_model)


# Fast-path routing keyword heuristic (replaces classify_intent() API call)
_REASONING_KEYWORDS = {
    "architect", "design", "strategy", "patent", "research", "analyze",
    "why does", "explain deeply", "deep dive", "investigate", "compare",
    "trade-off", "tradeoff", "plan", "system design", "how does", "theorem", "proof",
    "roadmap", "architecture", "timeline", "business", "product", "discussion",
    "proposal", "workflow", "audit", "evaluate", "diagnose", "diagnostic",
    "security", "threat model", "risk", "harden", "intelligence", "assessment"
}

_HIGH_CONFIDENCE_REASONING_PHRASES = {
    "deep dive",
    "explain deeply",
    "how does",
    "why does",
    "system design",
    "threat model",
    "trade-off",
    "tradeoff",
}

_LOW_CONFIDENCE_REASONING_WORDS = {
    "business",
    "discussion",
    "product",
    "timeline",
}

_SIMPLE_TASK_HINTS = {
    "add",
    "change",
    "fix",
    "format",
    "rename",
    "replace",
    "update",
}

_COMPLEXITY_HINTS = {
    "across",
    "architecture",
    "best",
    "complex",
    "multi-step",
    "multiple",
    "risk",
    "system",
    "tradeoffs",
}


def _contains_keyword(prompt_lower: str, keyword: str) -> bool:
    if " " in keyword or "-" in keyword:
        return keyword in prompt_lower
    return re.search(rf"\b{re.escape(keyword)}\b", prompt_lower) is not None


def routing_reason_score(prompt: str) -> int:
    """Score whether a prompt benefits from the higher-intelligence route.

    This keeps routing local and cheap, but avoids escalating every prompt that
    happens to contain a broad word such as "product" or "timeline".
    """
    prompt_lower = prompt.lower()
    if not prompt_lower.strip():
        return 0

    score = 0
    for keyword in _REASONING_KEYWORDS:
        if not _contains_keyword(prompt_lower, keyword):
            continue
        if keyword in _HIGH_CONFIDENCE_REASONING_PHRASES:
            score += 3
        elif keyword in _LOW_CONFIDENCE_REASONING_WORDS:
            score += 1
        else:
            score += 2

    if len(prompt_lower) > 280:
        score += 1
    if any(_contains_keyword(prompt_lower, hint) for hint in _COMPLEXITY_HINTS):
        score += 1
    if (
        any(_contains_keyword(prompt_lower, hint) for hint in _SIMPLE_TASK_HINTS)
        and score <= 2
        and len(prompt_lower) < 180
    ):
        score -= 2
    return max(score, 0)


# ─── Telemetry-Informed Routing Prior ────────────────────────────────────────
def _advisor_margin() -> float:
    try:
        return max(0.0, float(os.environ.get("UNIGROK_ADVISOR_MARGIN", "0.15")))
    except ValueError:
        return 0.15


def _calibration_ttl_hours() -> float:
    """Freshness window for eval-derived routing_calibration rows (default
    168 h = 7 days). Rows older than this never take precedence."""
    try:
        return max(0.0, float(os.environ.get("UNIGROK_CALIBRATION_TTL_HOURS", "168")))
    except ValueError:
        return 168.0


@dataclass
class RoutingDecision:
    """Diagnostic record of the advisor's last borderline decision.

    source precedence: calibration > semantic > telemetry > static.
    shadow=True marks a decision where a semantic verdict WAS computed but
    the baseline was returned (UNIGROK_TASK_RAG=shadow — zero production
    impact by construction)."""

    source: str
    prefers_planning: bool
    applied: bool
    shadow: bool = False
    evidence_count: int = 0
    planning_signal: float = 0.0
    coding_signal: float = 0.0
    confidence: float = 0.0
    at: float = field(default_factory=time.time)


class RoutingAdvisor:
    """Telemetry-informed prior for BORDERLINE routing scores (score == 1).

    Borderline prompts statically fall to the coding model. This advisor can
    flip one to the planning model, consulting three data sources in strict
    precedence order:

      1. EVAL CALIBRATION (routing_calibration table, written by
         `python -m evals run`): rows fresh within
         UNIGROK_CALIBRATION_TTL_HOURS (default 168) whose n >= _CALIB_MIN_N
         are aggregated per model; when BOTH models have eligible rows the
         calibration verdict is final — curated golden-task outcomes beat raw
         telemetry.
      2. SEMANTIC TASK-MEMORY EVIDENCE (src/rag.py, only when
         UNIGROK_TASK_RAG is shadow|active and calibration was undecidable):
         fused local-FTS + collection matches for THIS prompt, weighted by
         per-model success. In shadow mode the verdict is recorded but the
         baseline is returned; in active mode a decidable verdict is final
         (a decidable False blocks a telemetry flip, mirroring calibration).
      3. RAW TELEMETRY fallback: the most recent task-memory rows
         (store.get_recent_model_stats, last 200) aggregated into per-model
         success rates; flips only when planning's recent success rate
         exceeds the coding model's by UNIGROK_ADVISOR_MARGIN (default 0.15)
         AND both models have at least _MIN_SAMPLES recent rows.

    Both aggregates are cached in-process for _TTL_SEC, so the routing hot
    path performs zero extra DB reads between refreshes. Under
    UNI_GROK_TESTING the advisor is bypassed entirely (returns the static
    prior) unless a test injects data via inject_stats()/inject_calibration()/
    inject_semantic() — offline evals and cassettes stay byte-identical.
    """

    _TTL_SEC = 120.0
    _MIN_SAMPLES = 20
    _WINDOW_ROWS = 200
    _CALIB_MIN_N = 5

    def __init__(self, ttl: float = _TTL_SEC):
        self._ttl = ttl
        self._stats: Optional[List[Dict[str, Any]]] = None
        self._fetched_at = 0.0
        self._injected = False
        self._calibration: Optional[List[Dict[str, Any]]] = None
        self._calibration_fetched_at = 0.0
        self._calibration_injected = False
        self._semantic: Optional[Any] = None
        self._semantic_injected = False
        self._last_decision: Optional[RoutingDecision] = None
        self._lock = asyncio.Lock()

    def inject_stats(self, stats: List[Dict[str, Any]]):
        """Test hook: pin the aggregate; refresh is skipped while injected."""
        self._stats = list(stats or [])
        self._fetched_at = time.time()
        self._injected = True

    def inject_calibration(self, rows: List[Dict[str, Any]]):
        """Test hook: pin eval calibration rows; overrides the UNI_GROK_TESTING
        bypass so tests exercise the precedence path explicitly."""
        self._calibration = list(rows or [])
        self._calibration_fetched_at = time.time()
        self._calibration_injected = True

    def inject_semantic(self, verdict: Optional[Any]):
        """Test hook: pin the semantic verdict (a rag.SemanticVerdict);
        overrides the UNI_GROK_TESTING bypass so tests exercise the
        precedence path explicitly."""
        self._semantic = verdict
        self._semantic_injected = True

    def invalidate(self):
        self._stats = None
        self._fetched_at = 0.0
        self._injected = False
        self._calibration = None
        self._calibration_fetched_at = 0.0
        self._calibration_injected = False
        self._semantic = None
        self._semantic_injected = False
        self._last_decision = None

    async def _snapshot(self, store: Any) -> List[Dict[str, Any]]:
        if self._injected:
            return self._stats or []
        if os.environ.get("UNI_GROK_TESTING") == "1":
            # Hermetic: never aggregate the real store under tests.
            return []
        if store is None:
            return self._stats or []
        if self._stats is not None and (time.time() - self._fetched_at) < self._ttl:
            return self._stats
        async with self._lock:
            if self._stats is not None and (time.time() - self._fetched_at) < self._ttl:
                return self._stats
            try:
                self._stats = await store.get_recent_model_stats(self._WINDOW_ROWS)
            except Exception as exc:
                logging.getLogger("GrokMCP").warning(f"Routing advisor refresh failed: {exc}")
                self._stats = self._stats or []
            # Failures are cached for the TTL too — an unavailable store must
            # not re-probe on every borderline prompt.
            self._fetched_at = time.time()
        return self._stats

    async def _calibration_snapshot(self, store: Any) -> List[Dict[str, Any]]:
        """Fresh eval-calibration rows (updated_at within the TTL window).
        Mirrors _snapshot's caching/bypass rules; a store predating the v6
        migration (or any read failure) degrades to an empty list."""
        if self._calibration_injected:
            return self._calibration or []
        if os.environ.get("UNI_GROK_TESTING") == "1":
            # Hermetic: never read the real store under tests.
            return []
        if store is None:
            return self._calibration or []
        if self._calibration is not None and (time.time() - self._calibration_fetched_at) < self._ttl:
            return self._calibration
        async with self._lock:
            if self._calibration is not None and (time.time() - self._calibration_fetched_at) < self._ttl:
                return self._calibration
            try:
                self._calibration = await store.get_routing_calibration(
                    max_age_hours=_calibration_ttl_hours()
                )
            except Exception as exc:
                logging.getLogger("GrokMCP").warning(
                    f"Routing calibration refresh failed: {exc}"
                )
                self._calibration = self._calibration or []
            self._calibration_fetched_at = time.time()
        return self._calibration

    @classmethod
    def _aggregate_calibration(cls, rows: List[Dict[str, Any]], model: str) -> tuple:
        """Collapse a model's calibration rows into (samples, success_rate,
        avg_cost). Only rows with n >= _CALIB_MIN_N are eligible — a couple of
        eval outcomes must not steer routing."""
        samples = 0
        successes = 0.0
        cost_total = 0.0
        for row in rows:
            if str(row.get("model") or "") != model:
                continue
            n = int(row.get("n") or 0)
            if n < cls._CALIB_MIN_N:
                continue
            samples += n
            successes += float(row.get("success_rate") or 0.0) * n
            cost_total += float(row.get("avg_cost_usd") or 0.0) * n
        if samples <= 0:
            return 0, 0.0, 0.0
        return samples, successes / samples, cost_total / samples

    def _decide_calibration(
        self, rows: List[Dict[str, Any]], planning_model: str, coding_model: str
    ) -> Optional[bool]:
        """Calibration verdict for a borderline prompt: True/False when BOTH
        models have eligible fresh rows (that verdict is final), None when
        calibration cannot decide and telemetry should be consulted."""
        if not rows:
            return None
        p_samples, p_rate, _ = self._aggregate_calibration(rows, planning_model)
        c_samples, c_rate, _ = self._aggregate_calibration(rows, coding_model)
        if p_samples <= 0 or c_samples <= 0:
            return None
        return (p_rate - c_rate) >= _advisor_margin()

    @staticmethod
    def _aggregate(stats: List[Dict[str, Any]], model: str) -> tuple:
        """Collapse a model's per-plane rows into (samples, success_rate, avg_cost)."""
        samples = 0
        successes = 0.0
        cost_total = 0.0
        for row in stats:
            if str(row.get("model") or "") != model:
                continue
            n = int(row.get("samples") or 0)
            if n <= 0:
                continue
            samples += n
            successes += float(row.get("success_rate") or 0.0) * n
            cost_total += float(row.get("avg_cost") or 0.0) * n
        if samples <= 0:
            return 0, 0.0, 0.0
        return samples, successes / samples, cost_total / samples

    def _decide(self, stats: List[Dict[str, Any]], planning_model: str, coding_model: str) -> bool:
        if not stats:
            return False
        p_samples, p_rate, _ = self._aggregate(stats, planning_model)
        c_samples, c_rate, _ = self._aggregate(stats, coding_model)
        if p_samples < self._MIN_SAMPLES or c_samples < self._MIN_SAMPLES:
            return False
        return (p_rate - c_rate) >= _advisor_margin()

    async def _semantic_verdict(
        self,
        store: Any,
        prompt: str,
        context_id: Optional[str],
        planning_model: str,
        coding_model: str,
    ) -> Optional[Any]:
        if self._semantic_injected:
            return self._semantic
        if os.environ.get("UNI_GROK_TESTING") == "1":
            # Hermetic: like _snapshot/_calibration_snapshot, semantic
            # evidence is inert under tests unless injected — offline evals,
            # cassettes and the seed suite stay byte-identical.
            return None
        try:
            from .rag import gather_semantic_evidence

            return await gather_semantic_evidence(
                store, prompt, context_id, planning_model, coding_model
            )
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(
                f"Semantic routing evidence failed (fail-open): {exc}"
            )
            return None

    async def prefers_planning(
        self,
        store: Any,
        planning_model: str,
        coding_model: str,
        prompt: Optional[str] = None,
        context_id: Optional[str] = None,
    ) -> bool:
        """True only when fresh eval calibration (first), a decidable
        semantic task-memory verdict (UNIGROK_TASK_RAG=active only), or
        recent telemetry (fallback) justifies flipping a borderline prompt
        to the planning model; anything else keeps the static prior. The
        3-arg legacy call (no prompt) behaves exactly as before semantic
        evidence existed."""
        try:
            # 1. EVAL CALIBRATION — a decidable verdict is always final.
            calibration = await self._calibration_snapshot(store)
            verdict = self._decide_calibration(calibration, planning_model, coding_model)
            if verdict is not None:
                self._last_decision = RoutingDecision(
                    source="calibration", prefers_planning=verdict, applied=True
                )
                return verdict

            # Baseline = the pre-semantic fallback chain (telemetry → static).
            stats = await self._snapshot(store)
            baseline = self._decide(stats, planning_model, coding_model)
            baseline_source = "telemetry" if stats else "static"

            # 2. SEMANTIC TASK-MEMORY EVIDENCE — only when calibration was
            #    undecidable, a prompt is available, and the task-RAG mode
            #    is shadow|active (off/mirror never reach src/rag retrieval,
            #    keeping those modes byte-identical to the legacy chain).
            mode = "off"
            semantic = None
            if prompt:
                from .rag import task_rag_mode

                mode = task_rag_mode()
                if mode in ("shadow", "active"):
                    semantic = await self._semantic_verdict(
                        store, prompt, context_id, planning_model, coding_model
                    )
            if semantic is not None and semantic.prefers_planning is not None:
                from .rag import record_stat

                if mode == "active":
                    # A decidable semantic verdict is final either way — a
                    # decidable False BLOCKS a telemetry flip, mirroring the
                    # calibration semantics.
                    if semantic.prefers_planning != baseline:
                        record_stat("applied_flips")
                    self._last_decision = RoutingDecision(
                        source="semantic",
                        prefers_planning=semantic.prefers_planning,
                        applied=True,
                        evidence_count=semantic.evidence_count,
                        planning_signal=semantic.planning_signal,
                        coding_signal=semantic.coding_signal,
                        confidence=semantic.confidence,
                    )
                    return semantic.prefers_planning
                # SHADOW: compute + record, NEVER apply — return the baseline.
                if semantic.prefers_planning != baseline:
                    record_stat("shadow_flips")
                self._last_decision = RoutingDecision(
                    source=baseline_source,
                    prefers_planning=baseline,
                    applied=True,
                    shadow=True,
                    evidence_count=semantic.evidence_count,
                    planning_signal=semantic.planning_signal,
                    coding_signal=semantic.coding_signal,
                    confidence=semantic.confidence,
                )
                return baseline

            # 3./4. TELEMETRY → STATIC (unchanged semantics).
            self._last_decision = RoutingDecision(
                source=baseline_source, prefers_planning=baseline, applied=True
            )
            return baseline
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(f"Routing advisor decision failed: {exc}")
            return False

    async def status_view(self, store: Any) -> Dict[str, Any]:
        """The advisor's current view, for grok_mcp_status."""
        planning_model = await resolve_model("planning")
        coding_model = await resolve_model("coding")
        stats = await self._snapshot(store)
        calibration = await self._calibration_snapshot(store)
        p_samples, p_rate, p_cost = self._aggregate(stats, planning_model)
        c_samples, c_rate, c_cost = self._aggregate(stats, coding_model)
        cal_p_samples, cal_p_rate, cal_p_cost = self._aggregate_calibration(calibration, planning_model)
        cal_c_samples, cal_c_rate, cal_c_cost = self._aggregate_calibration(calibration, coding_model)
        cal_verdict = self._decide_calibration(calibration, planning_model, coding_model)
        prefers = cal_verdict if cal_verdict is not None else self._decide(stats, planning_model, coding_model)
        # Task-memory RAG view (src/rag.py). `ready` is the CACHED readiness
        # from the most recent probe/upload/search — never a network call on
        # the status path; `unsynced` is a cheap partial-index COUNT guarded
        # so a failing store can never break /metrics.
        task_rag_view: Optional[Dict[str, Any]] = None
        try:
            from .rag import (
                FUSED_SCORE_BUCKETS,
                get_task_memory_mirror,
                get_task_rag_stats,
                task_rag_collection_name,
                task_rag_mode,
            )

            rag_mode = task_rag_mode()
            unsynced: Optional[int] = None
            if rag_mode != "off" and store is not None:
                try:
                    unsynced = await store.count_unsynced_task_memories()
                except Exception:
                    unsynced = None
            task_rag_view = {
                "mode": rag_mode,
                "collection": task_rag_collection_name(),
                "ready": get_task_memory_mirror().last_known_ready,
                "unsynced": unsynced,
                "fused_score_bucket_bounds": list(FUSED_SCORE_BUCKETS),
                **get_task_rag_stats(),
            }
        except Exception as exc:
            logging.getLogger("GrokMCP").warning(f"task_rag status view failed: {exc}")
        return {
            "planning_model": planning_model,
            "coding_model": coding_model,
            "planning": {"samples": p_samples, "success_rate": p_rate, "avg_cost": p_cost},
            "coding": {"samples": c_samples, "success_rate": c_rate, "avg_cost": c_cost},
            "margin": _advisor_margin(),
            "min_samples": self._MIN_SAMPLES,
            "calibration": {
                "source_active": cal_verdict is not None,
                "ttl_hours": _calibration_ttl_hours(),
                "min_n": self._CALIB_MIN_N,
                "planning": {"samples": cal_p_samples, "success_rate": cal_p_rate, "avg_cost": cal_p_cost},
                "coding": {"samples": cal_c_samples, "success_rate": cal_c_rate, "avg_cost": cal_c_cost},
            },
            "borderline_source": "calibration" if cal_verdict is not None else "telemetry",
            "borderline_choice": "planning" if prefers else "coding (static prior)",
            # Per-request record (calibration|semantic|telemetry|static);
            # shadow=True means a semantic verdict was computed but the
            # baseline was returned (UNIGROK_TASK_RAG=shadow).
            "last_decision": asdict(self._last_decision) if self._last_decision else None,
            "task_rag": task_rag_view,
        }


_ROUTING_ADVISOR = RoutingAdvisor()


def get_routing_advisor() -> RoutingAdvisor:
    return _ROUTING_ADVISOR


# ─── Structured Reflection (thinking route) ──────────────────────────────────
# Replaces the retired string-scanning reflection parser: the reviewer verdict
# is schema-enforced by chat.parse(ReflectionVerdict), so keyword lists and
# regex JSON extraction are gone by design.

async def _parse_structured(
    shape: Any,
    system_prompt: str,
    user_prompt: str,
    model: str,
    timeout: float,
    logger: Optional[logging.Logger] = None,
) -> tuple[Optional[Any], int, float]:
    """Tool-free structured parse shared by the reflection reviewer and the
    knowledge distiller (do-not-duplicate seam).

    Builds a DEDICATED TOOL-FREE chat (no tools kwarg — chat.parse() has no
    tool loop, so a tool call emitted mid-parse surfaces as a validation
    error) and parses `shape` via structured outputs. Returns
    (parsed, tokens, cost); parsed=None means the capability is unavailable
    (missing parse(), parse/validation error, or timeout) and callers must
    degrade gracefully — string scanning is never used.
    """
    log = logger or logging.getLogger("GrokMCP")

    def _parse():
        from xai_sdk.chat import system, user

        client = get_xai_client()
        # TOOL-FREE by construction: no tools kwarg is ever passed here.
        chat = client.chat.create(model=model)
        if not hasattr(chat, "parse"):
            return None
        chat.append(system(system_prompt))
        chat.append(user(user_prompt))
        return chat.parse(shape)

    try:
        parsed = await run_blocking(_parse, timeout=timeout)
    except Exception as exc:
        log.warning(
            f"Structured parse ({getattr(shape, '__name__', shape)}) failed: {exc}"
        )
        return None, 0, 0.0
    if parsed is None:
        log.warning("Installed xai_sdk chat lacks parse(); structured parse skipped.")
        return None, 0, 0.0
    response, result = parsed
    tokens = 0
    usage = getattr(response, "usage", None)
    if usage:
        tokens = (
            getattr(usage, "prompt_tokens", 0) or 0
        ) + (getattr(usage, "completion_tokens", 0) or 0)
    cost = float(getattr(response, "cost_usd", 0.0) or 0.0)
    return result, tokens, cost


class ReflectionVerdict(BaseModel):
    """Schema-enforced reviewer verdict for the thinking route."""

    status: Literal["pass", "fail"]
    issues: List[str] = PydanticField(default_factory=list)
    next_action: str = ""


_REFLECTION_SYS_PROMPT = (
    "You are a strict reviewer. Judge whether the candidate answer fully "
    "satisfies the original request. Return status='pass' only when the "
    "answer is correct, complete, and consistent with the tool evidence. "
    "When status='fail', list the concrete issues and state the single most "
    "useful next_action."
)


def _reflect_max_iterations() -> int:
    """Reviewer-driven retry ceiling for the thinking route (default 2)."""
    try:
        return max(0, int(os.environ.get("UNIGROK_REFLECT_MAX_ITERATIONS", "2")))
    except ValueError:
        return 2


async def _reflect_on_answer(
    prompt: str,
    answer: str,
    tool_trace: List[Dict[str, Any]],
    model: str,
) -> tuple[Optional[ReflectionVerdict], int, float]:
    """Schema-enforced reviewer pass over a candidate answer.

    Rides the shared tool-free structured-parse machinery
    (_parse_structured) to obtain a ReflectionVerdict. Returns
    (verdict, tokens, cost); verdict=None means the reviewer is unavailable
    (missing parse capability, parse/validation error, or timeout) and the
    caller must ACCEPT the answer — string scanning is never used.
    """
    logger = logging.getLogger("GrokMCP.ThinkingLoop")
    trace_block = format_tool_trace_block(tool_trace) if tool_trace else ""
    review_prompt = (
        "Original request:\n"
        f"{prompt[:8000]}\n\n"
        "Candidate answer:\n"
        f"{answer[:12000]}\n\n"
        "Tool evidence:\n"
        f"{trace_block or 'No tools were used for this answer.'}"
    )

    verdict, tokens, cost = await _parse_structured(
        ReflectionVerdict,
        _REFLECTION_SYS_PROMPT,
        review_prompt,
        model,
        timeout=_env_timeout("UNIGROK_REFLECT_TIMEOUT", 60.0),
        logger=logger,
    )
    if verdict is None:
        logger.warning("Structured reflection unavailable; accepting answer.")
    return verdict, tokens, cost


# ─── Knowledge Distillation (session transcript → durable facts) ─────────────
class FactList(BaseModel):
    """Schema-enforced distillation output: 3-8 durable, standalone facts
    (parsed via the same tool-free structured-parse machinery as
    ReflectionVerdict — see _parse_structured)."""

    facts: List[str] = PydanticField(min_length=3, max_length=8)


_DISTILL_SYS_PROMPT = (
    "You distill conversations into durable workspace knowledge. Extract 3-8 "
    "standalone facts worth remembering across future sessions: decisions, "
    "constraints, file paths, tool/model behaviors, user preferences, and "
    "verified findings. Each fact must be one self-contained sentence with "
    "concrete specifics. Exclude transient chatter, greetings, and anything "
    "true only for this single exchange."
)


def _auto_distill_enabled() -> bool:
    """UNIGROK_AUTO_DISTILL=1 enables submit-on-threshold auto-distillation.
    OFF by default — distillation is a paid model call."""
    return os.environ.get("UNIGROK_AUTO_DISTILL", "").strip().lower() in ("1", "true", "yes")


def _auto_distill_min_messages() -> int:
    """History length (messages) that triggers ONE auto-distill submission
    per session per process (UNIGROK_AUTO_DISTILL_MIN_MESSAGES, default 12)."""
    try:
        return max(2, int(os.environ.get("UNIGROK_AUTO_DISTILL_MIN_MESSAGES", "12")))
    except ValueError:
        return 12


# Sessions already auto-distilled by THIS process — the at-most-once guard.
_AUTO_DISTILLED_SESSIONS: set = set()


# ─── xAI Collections Adapter (UNIGROK_COLLECTIONS, capability-gated) ─────────
# Optional cloud mirror for the LOCAL knowledge table. The local store is the
# source of truth and works everywhere; with UNIGROK_COLLECTIONS=1 AND an
# installed xai_sdk exposing the collections service (verified on 1.17.0:
# client.collections.create/list/upload_document/search), new facts sync
# best-effort into a named collection and search_knowledge merges collection
# matches into its results. Failures are logged ONCE and never raise — the
# hot path (get_dynamic_context) never consults the collection at all.

_COLLECTIONS_LOCK = threading.Lock()
_KNOWLEDGE_COLLECTION_ID: Optional[str] = None
_COLLECTIONS_WARNED = False


def _collections_enabled() -> bool:
    return os.environ.get("UNIGROK_COLLECTIONS", "").strip().lower() in ("1", "true", "yes")


def _knowledge_collection_name() -> str:
    return os.environ.get("UNIGROK_COLLECTION_NAME", "").strip() or "unigrok-knowledge"


def _collections_capable(client: Any) -> bool:
    """Capability gate: the installed xai_sdk must expose the collections
    service surface this adapter uses (create/list/upload_document/search)."""
    service = getattr(client, "collections", None)
    if service is None:
        return False
    return all(
        callable(getattr(service, name, None))
        for name in ("create", "list", "upload_document", "search")
    )


def _warn_collections_once(exc: Exception):
    global _COLLECTIONS_WARNED
    if not _COLLECTIONS_WARNED:
        _COLLECTIONS_WARNED = True
        logging.getLogger("GrokMCP").warning(
            f"Collections knowledge sync unavailable (logged once): {exc}"
        )


def _resolve_knowledge_collection_id(client: Any) -> Optional[str]:
    """Find-or-create the named knowledge collection; the id is cached for
    the process lifetime. Runs on an executor thread (SDK calls are sync)."""
    global _KNOWLEDGE_COLLECTION_ID
    with _COLLECTIONS_LOCK:
        if _KNOWLEDGE_COLLECTION_ID:
            return _KNOWLEDGE_COLLECTION_ID
        name = _knowledge_collection_name()
        listing = client.collections.list(limit=100)
        for meta in getattr(listing, "collections", None) or []:
            if str(getattr(meta, "collection_name", "") or "") == name:
                _KNOWLEDGE_COLLECTION_ID = str(meta.collection_id)
                return _KNOWLEDGE_COLLECTION_ID
        created = client.collections.create(name=name)
        collection_id = str(getattr(created, "collection_id", "") or "")
        _KNOWLEDGE_COLLECTION_ID = collection_id or None
        return _KNOWLEDGE_COLLECTION_ID


async def sync_fact_to_collection(
    fact_id: Any,
    fact: str,
    scope: str = "global",
    source: str = "",
) -> bool:
    """Best-effort mirror of ONE saved fact into the xAI knowledge collection.

    No-op (False) unless UNIGROK_COLLECTIONS=1 and the installed SDK is
    capable; never raises and never blocks the caller beyond the bounded
    UNIGROK_COLLECTIONS_TIMEOUT. This is the adapter seam: local-first
    callers (distill job, remember_fact) fire it after the local save."""
    if not _collections_enabled():
        return False

    def _upload():
        client = get_xai_client()
        if not _collections_capable(client):
            raise RuntimeError("installed xai_sdk lacks the collections service surface")
        collection_id = _resolve_knowledge_collection_id(client)
        if not collection_id:
            raise RuntimeError("knowledge collection could not be resolved")
        client.collections.upload_document(
            collection_id,
            f"fact-{fact_id}-{_task_hash(str(fact))[:8]}.txt",
            str(fact or "").encode("utf-8"),
        )
        return True

    try:
        return bool(await run_blocking(
            _upload, timeout=_env_timeout("UNIGROK_COLLECTIONS_TIMEOUT", 15.0)
        ))
    except Exception as exc:
        _warn_collections_once(exc)
        return False


async def search_knowledge_collection(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Best-effort search passthrough over the knowledge collection; results
    (chunk content + score, origin='collection') merge into search_knowledge.
    Returns [] unless enabled and capable; never raises."""
    if not _collections_enabled():
        return []

    def _search():
        client = get_xai_client()
        if not _collections_capable(client):
            raise RuntimeError("installed xai_sdk lacks the collections service surface")
        collection_id = _resolve_knowledge_collection_id(client)
        if not collection_id:
            return []
        response = client.collections.search(
            str(query or ""), [collection_id], limit=max(1, min(int(limit or 5), 25))
        )
        results = []
        for match in getattr(response, "matches", None) or []:
            content = str(getattr(match, "chunk_content", "") or "").strip()
            if not content:
                continue
            results.append({
                "fact": content[:1000],
                "score": float(getattr(match, "score", 0.0) or 0.0),
                "origin": "collection",
                "file_id": str(getattr(match, "file_id", "") or ""),
            })
        return results

    try:
        return await run_blocking(
            _search, timeout=_env_timeout("UNIGROK_COLLECTIONS_TIMEOUT", 15.0)
        )
    except Exception as exc:
        _warn_collections_once(exc)
        return []


async def run_thinking_loop(
    prompt: str,
    session: Optional[str] = None,
    store: Any = None,
    dynamic_sys_prompt: str = "",
    model: str = DEFAULT_PLANNING_MODEL,
    context_id: Optional[str] = None,
    max_reflections: Optional[int] = None,
    global_budget_usd: Optional[float] = None,
    profile: Optional[Dict[str, Any]] = None,
    input_messages: Optional[List[Dict[str, Any]]] = None,
    on_event: Optional[Callable] = None,
    caller: Optional[str] = None,
) -> MetaLayer:
    """Thinking route: AgentLoop execution wrapped in a schema-enforced
    reflection loop. Replaces the retired 6-stage ThinkingKernel.

    caller attributes this route's telemetry row (per-caller budgets/metrics
    count thinking-mode spend like every other route); orchestrate threads
    the identity it resolved, and save_telemetry additionally falls back to
    the ambient contextvar when the param stays None.

    Each attempt runs the full ReAct AgentLoop; a dedicated tool-free reviewer
    chat then parses a ReflectionVerdict via chat.parse() (structured
    outputs). status='fail' feeds the issues back into a fresh AgentLoop
    attempt — up to max_reflections retries (default
    UNIGROK_REFLECT_MAX_ITERATIONS=2) — while cost accumulates across attempts
    against ONE shared budget (AgentLoopPolicy.global_budget_usd semantics).
    An unavailable reviewer accepts the answer as-is.
    """
    start_time = time.time()
    active_profile = profile or load_grok_profile(model)
    base_policy = AgentLoopPolicy()
    if global_budget_usd is not None:
        base_policy.global_budget_usd = global_budget_usd
    budget = base_policy.global_budget_usd
    retries = max_reflections if max_reflections is not None else _reflect_max_iterations()

    layer = MetaLayer()
    total_cost = 0.0
    total_tokens = 0
    reasoning_parts: List[str] = []
    reflection_notes: List[str] = []
    current_prompt = prompt
    correction: Optional[str] = None
    history = (await load_history(session)) if session and not input_messages else None

    for iteration in range(retries + 1):
        attempt_policy = replace(
            base_policy, global_budget_usd=max(budget - total_cost, 0.0)
        )
        loop = AgentLoop(
            policy=attempt_policy,
            dynamic_sys_prompt=dynamic_sys_prompt,
            model=model,
            store=store,
            profile=active_profile,
            on_event=on_event,
        )
        if input_messages is not None and iteration > 0:
            # Reviewer-driven retries must keep the caller's full conversation
            # (the prior turns define what the task means): the correction
            # rides as an extra trailing user turn instead of dropping
            # input_messages, which would strip every earlier turn from the
            # retry's chat.
            attempt_messages = list(input_messages) + [
                {"role": "user", "content": correction or current_prompt}
            ]
        else:
            attempt_messages = input_messages
        attempt = await loop.run(
            current_prompt,
            session,
            history=history,
            input_messages=attempt_messages,
        )
        total_cost += attempt.cost_usd
        total_tokens += attempt.tokens
        layer.generation = attempt.generation
        layer.tool_trace = attempt.tool_trace
        layer.finish_reason = attempt.finish_reason
        # The accepted attempt's stored-completion id is the session's next
        # previous_response_id (empty when server state was not used).
        layer.response_id = attempt.response_id
        # Any attempt self-escalating marks the whole thinking run.
        layer.escalated = layer.escalated or attempt.escalated
        if attempt.reasoning:
            reasoning_parts.append(f"[Attempt {iteration + 1}]:\n{attempt.reasoning}")
        if iteration == 0:
            # Plan = the first attempt's opening reasoning segment.
            layer.plan = (attempt.reasoning or "").split("\n\n---\n\n")[0][:4000]

        if attempt.finish_reason == "budget_exhausted" or total_cost >= budget:
            layer.finish_reason = "budget_exhausted"
            reflection_notes.append(
                f"[Budget]: stopped after attempt {iteration + 1}; "
                f"cost ${total_cost:.4f} reached limit ${budget:.2f}."
            )
            break

        verdict, r_tokens, r_cost = await _reflect_on_answer(
            prompt, attempt.generation, attempt.tool_trace, model
        )
        total_tokens += r_tokens
        total_cost += r_cost
        if verdict is None:
            # Reviewer unavailable — accept the answer as-is (never string-scan).
            reflection_notes.append(
                f"[Attempt {iteration + 1}]: reviewer unavailable; "
                "answer accepted without review."
            )
            break
        if verdict.status == "pass":
            layer.finish_reason = "final_answer"
            reflection_notes.append(f"[Attempt {iteration + 1}]: verdict=pass.")
            break
        issues_text = "; ".join(issue for issue in verdict.issues if issue) or "unspecified issues"
        reflection_notes.append(
            f"[Attempt {iteration + 1}]: verdict=fail; issues: {issues_text}; "
            f"next_action: {verdict.next_action or 'none provided'}"
        )
        if total_cost >= budget:
            layer.finish_reason = "budget_exhausted"
            reflection_notes.append(
                f"[Budget]: stopped after reflection {iteration + 1}; "
                f"cost ${total_cost:.4f} reached limit ${budget:.2f}."
            )
            break
        correction = (
            f"Reviewer found: {issues_text}\n"
            f"Suggested next action: {verdict.next_action or 'address the issues above'}\n"
            "Fix every issue above and produce a corrected final answer."
        )
        current_prompt = f"{prompt}\n\n{correction}"
    else:
        # All reviewer-driven retries consumed with the last verdict failing.
        layer.finish_reason = "depth_exhausted"

    layer.reasoning = "\n\n".join(reasoning_parts)
    layer.reflection = "\n\n".join(reflection_notes)
    layer.tokens = total_tokens
    layer.cost_usd = total_cost
    layer.plane = "API"
    layer.route = "thinking"
    layer.profile = str(active_profile.get("profile") or "")
    layer.policy_mode = current_policy_mode()
    layer.context_id = context_id
    layer.latency = time.time() - start_time
    if store:
        await store.save_telemetry(
            prompt[:100],
            layer.plane,
            1 if layer.finish_reason == "final_answer" else 0,
            layer.latency,
            total_cost,
            context_id=context_id,
            caller=caller,
        )
    return layer


_TOOLS_MANIFEST_CACHE = GitContextCache(ttl=30.0)

async def get_tools_manifest(mcp_instance: Any = None) -> str:
    if not mcp_instance:
        return ""
    cached = _TOOLS_MANIFEST_CACHE.get("tools_manifest")
    if cached is not None:
        return cached
    try:
        tools = await mcp_instance.list_tools()
        lines = [
            "### Available UniGrok MCP Tools",
            "Use these tools only when they directly support the user's request and current runtime policy:",
        ]
        for t in tools:
            if t.name in ["chat", "list_chat_sessions", "get_chat_history", "clear_chat_history"]:
                continue
            desc = t.description or "No description provided."
            lines.append(f"- **Subagent `{t.name}`**: {desc}")
            lines.append(f"  Input Schema: {t.inputSchema}")
        res = "\n".join(lines)
        _TOOLS_MANIFEST_CACHE.set("tools_manifest", res)
        return res
    except Exception as e:
        return f"Subagent discovery unavailable: {e}"


def _image_part_to_sdk_content(part: Dict[str, Any]):
    from xai_sdk.chat import image as sdk_image

    image_value = part.get("image_url") or part.get("url")
    detail = part.get("detail")
    if isinstance(image_value, dict):
        detail = image_value.get("detail", detail)
        image_value = image_value.get("url")
    if not image_value:
        return None
    if detail not in ("auto", "low", "high", None):
        detail = "auto"
    return sdk_image(str(image_value), detail=detail or "auto")


def _message_content_to_sdk_args(content: Any) -> List[Any]:
    parts: List[Any] = []

    def add_text(value: Any):
        text_value = str(value).strip()
        if text_value:
            parts.append(text_value)

    def add_part(part: Any):
        if isinstance(part, dict):
            part_type = str(part.get("type", "")).lower()
            if "image_url" in part or part_type in ("image_url", "input_image"):
                image_content = _image_part_to_sdk_content(part)
                if image_content is not None:
                    parts.append(image_content)
                    return
            if "text" in part:
                add_text(part["text"])
                return
            if "content" in part:
                add_text(part["content"])
                return
            add_text(json.dumps(part, separators=(",", ":"), ensure_ascii=False))
            return
        if isinstance(part, list):
            for nested in part:
                add_part(nested)
            return
        add_text(part)

    if isinstance(content, list):
        for item in content:
            add_part(item)
    else:
        add_part(content)
    return parts


def _append_sdk_messages(chat: Any, messages: List[Dict[str, Any]], include_system: bool = True):
    from xai_sdk.chat import assistant, system, tool_result as sdk_tool_result, user

    for message in messages:
        role = str(message.get("role", "")).lower()
        content = message.get("content", "")
        if role == "system" and not include_system:
            continue
        if role == "tool":
            text_content = _message_content_to_text(content)
            if text_content:
                raw_tool_call_id = message.get("tool_call_id") or message.get("id")
                tool_call_id = raw_tool_call_id if isinstance(raw_tool_call_id, str) and raw_tool_call_id else None
                chat.append(sdk_tool_result(text_content, tool_call_id=tool_call_id))
            continue

        sdk_args = _message_content_to_sdk_args(content)
        if not sdk_args:
            continue
        if role == "system":
            chat.append(system(*sdk_args))
        elif role == "assistant":
            chat.append(assistant(*sdk_args))
        else:
            chat.append(user(*sdk_args))


# classify_intent() removed in Phase 1 refactor.
# Model selection is handled by the local _REASONING_KEYWORDS heuristic (zero-latency,
# zero-cost) in orchestrate(); execution defaults to the model self-directing
# inside AgentLoop.


def _parse_cli_json_output(raw: str) -> tuple[str, Optional[str]]:
    """Parse `grok -p --output-format json` output — one JSON object carrying
    {"text", "stopReason", "sessionId", "requestId"}. Anything that is not the
    expected object (older CLI builds, plain-text passthrough) is returned
    verbatim so the plane never fails on format drift."""
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return raw, None
        if isinstance(payload, dict) and "text" in payload:
            sid = payload.get("sessionId")
            return str(payload.get("text") or ""), (str(sid) if sid else None)
    return raw, None


def _format_cli_history_context(history: List[dict], max_messages: int = 10, max_chars: int = 6000) -> str:
    rows = []
    for message in history[-max_messages:]:
        role = str(message.get("role") or "user").lower()
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        content = _message_content_to_text(message.get("content", "")).strip()
        if not content:
            continue
        rows.append(f"{role}: {content[:900]}")
    if not rows:
        return ""
    body = "\n".join(rows)
    if len(body) > max_chars:
        body = body[-max_chars:]
    return (
        "# Server Conversation History\n"
        "Use this history as the authoritative memory for this MCP session.\n"
        f"{body}"
    )


async def _consume_cli_stream(proc, on_event) -> tuple[str, Optional[str], bytes]:
    """Consume `--output-format streaming-json` NDJSON from a grok CLI
    subprocess, forwarding each text delta to on_event as a content_delta
    event (the same shape the API plane streams). Returns
    (final_text, session_id, stderr). The caller bounds the whole read with
    one deadline via asyncio.wait_for."""
    stderr_task = asyncio.create_task(proc.stderr.read())
    parts: List[str] = []
    session_id: Optional[str] = None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="ignore").strip()
            if not decoded:
                continue
            try:
                event = json.loads(decoded)
            except json.JSONDecodeError:
                parts.append(decoded)
                continue
            etype = event.get("type") if isinstance(event, dict) else None
            if etype == "text":
                delta = str(event.get("data") or "")
                if delta:
                    parts.append(delta)
                    await _emit_agent_event(on_event, {"type": "content_delta", "text": delta})
            elif etype == "end":
                session_id = event.get("sessionId") or session_id
            # "thought" and unknown event types are progress-only: skipped.
        await proc.wait()
        stderr = await stderr_task
        return "".join(parts), session_id, stderr
    finally:
        if not stderr_task.done():
            stderr_task.cancel()


async def _call_plane(
    plane: ModelPlane,
    prompt: str,
    session: Optional[str] = None,
    store: Any = None,
    dynamic_sys_prompt: str = "",
    requested_model: Optional[str] = None,
    agent_count: Optional[int] = None,
    input_messages: Optional[List[Dict[str, Any]]] = None,
    profile: Optional[Dict[str, Any]] = None,
    on_event: Optional[Callable] = None,
    include: Optional[List[str]] = None,
    max_turns: Optional[int] = None,
    json_schema: Any = None,
) -> tuple[str, int, float, bool]:
    # Returns (content, tokens, cost, is_cli). When on_event is provided the
    # API branch streams for real via chat.stream(), forwarding each chunk as
    # a {"type": "content_delta", "text": ...} event before returning the
    # complete response as usual.
    from xai_sdk import Client
    from xai_sdk.chat import user, system, assistant

    if requested_model:
        model_name = requested_model
    elif plane in ["composer", "cli-fallback"]:
        model_name = "grok-composer-2.5-fast"
    else:
        model_name = await resolve_model("planning")

    is_cli = is_cli_model(model_name)

    if is_cli:
        if is_cloudrun_runtime():
            raise RuntimeError("Grok CLI execution is disabled in Cloud Run runtime.")

        grok_path = PathResolver.get_grok_cli_path()
        check_circuit_breaker(model_name)
        use_native_cli_session = bool(session and store and cli_native_session_ids_enabled())
        cli_prompt = prompt
        if session and not input_messages and not use_native_cli_session:
            history_context = _format_cli_history_context(await load_history(session, store))
            if history_context:
                cli_prompt = f"{history_context}\n\n# Current User Request\n{prompt}"

        async def _invoke_cli(cli_session_id: Optional[str]) -> tuple[str, Optional[str]]:
            output_format = "streaming-json" if on_event is not None else "json"
            args = _build_grok_cli_args(
                cli_prompt=cli_prompt,
                model_name=model_name,
                dynamic_sys_prompt=dynamic_sys_prompt,
                output_format=output_format,
                cli_session_id=cli_session_id,
                profile=profile or load_grok_profile(model_name),
                max_turns=max_turns,
                json_schema=json_schema,
            )

            proc = await asyncio.create_subprocess_exec(
                grok_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(PathResolver.get_project_root()),
            )
            cli_timeout = _env_timeout("UNIGROK_CLI_TIMEOUT", 120.0)
            try:
                if on_event is not None:
                    text, returned_id, stderr = await asyncio.wait_for(
                        _consume_cli_stream(proc, on_event), timeout=cli_timeout
                    )
                else:
                    stdout, stderr = await communicate_with_timeout(proc, cli_timeout)
                    text, returned_id = _parse_cli_json_output(
                        stdout.decode("utf-8", errors="ignore")
                    )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                raise RuntimeError(f"Grok CLI execution timed out after {cli_timeout:.1f} seconds")

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="ignore").strip()
                raise RuntimeError(f"Grok CLI error: {err_msg}")
            return text, returned_id

        async def _run_cli_mapped_session() -> tuple[str, Optional[str], Optional[str], Optional[str]]:
            stored_cli_id = None
            cli_session_id = None
            if use_native_cli_session:
                session_data = await store.get_session(session)
                if session_data:
                    stored_cli_id = session_data.get("cli_session_id")
                cli_session_id = stored_cli_id or str(uuid.uuid4())

            try:
                text, returned_id = await _invoke_cli(cli_session_id)
            except RuntimeError as exc:
                if not (use_native_cli_session and _is_cli_session_in_use_error(str(exc))):
                    raise
                retry_cli_id = str(uuid.uuid4())
                logging.getLogger("GrokMCP").warning(
                    f"Grok CLI session mapping for '{session}' was busy/stale; retrying with a fresh CLI session id."
                )
                text, returned_id = await _invoke_cli(retry_cli_id)
                cli_session_id = retry_cli_id

            return text, returned_id, cli_session_id if use_native_cli_session else None, stored_cli_id

        try:
            if session and store:
                async with _cli_logical_session_lock(session):
                    text, returned_id, cli_session_id, stored_cli_id = await _run_cli_mapped_session()
            else:
                text, returned_id, cli_session_id, stored_cli_id = await _run_cli_mapped_session()
        except RuntimeError as exc:
            if not _is_cli_session_in_use_error(str(exc)):
                record_xai_failure(model_name)
            raise

        record_xai_success(model_name)
        if use_native_cli_session:
            final_id = returned_id or cli_session_id
            if final_id and final_id != stored_cli_id:
                await store.save_session(session, cli_session_id=final_id, model=model_name)
        # Subscription plane: the CLI exposes no token usage and has no
        # per-token price, so tokens/cost stay 0 by design.
        return text, 0, 0.0, True
    else:
        history = (await load_history(session)) if session and not input_messages else []
        # Real streaming: chat.stream() is a SYNC iterator, so deltas are
        # bridged from the worker thread back onto the event loop with
        # run_coroutine_threadsafe (fire-and-forget; delivery order is
        # preserved because the callback body never awaits before handing
        # the event over).
        event_loop = asyncio.get_running_loop() if on_event is not None else None

        def _forward_delta(text: str):
            asyncio.run_coroutine_threadsafe(
                _emit_agent_event(on_event, {"type": "content_delta", "text": text}),
                event_loop,
            )

        def _call_api():
            client = get_xai_client()
            chat_params = {"model": model_name}
            active_profile = profile or load_grok_profile(model_name)
            if active_profile.get("temperature") is not None:
                chat_params["temperature"] = active_profile["temperature"]
            if active_profile.get("top_p") is not None:
                chat_params["top_p"] = active_profile["top_p"]
            if active_profile.get("reasoning_effort") and _chat_create_supports("reasoning_effort"):
                chat_params["reasoning_effort"] = active_profile["reasoning_effort"]
            if session and _chat_create_supports("conversation_id"):
                # Observability only: the SDK surfaces this as the
                # gen_ai.conversation.id span attribute so a session's calls
                # group in traces. It does NOT route or prompt-cache.
                chat_params["conversation_id"] = str(session)
            if agent_count is not None:
                chat_params["agent_count"] = agent_count
            if include and _chat_create_supports("include"):
                chat_params["include"] = list(include)
            grok = client.chat.create(**chat_params)
            grok.append(system(dynamic_sys_prompt))
            if input_messages:
                _append_sdk_messages(grok, input_messages, include_system=False)
            else:
                for msg in history:
                    if msg["role"] == "user":
                        grok.append(user(msg["content"]))
                    elif msg["role"] == "system":
                        # Compaction stores summaries as system-role entries.
                        grok.append(system(msg["content"]))
                    elif msg["role"] == "assistant":
                        grok.append(assistant(msg["content"]))
                grok.append(user(prompt))
            if on_event is not None and hasattr(grok, "stream"):
                # stream() yields (accumulated Response, Chunk); the last
                # accumulated Response is the complete one, so usage/cost
                # accounting below is identical to the sample() path.
                final_response = None
                for final_response, chunk in grok.stream():
                    delta = str(getattr(chunk, "content", "") or "")
                    if delta:
                        _forward_delta(delta)
                if final_response is None:
                    raise RuntimeError("chat.stream() yielded no chunks")
                return final_response
            res = grok.sample()
            return res

        # Per-model circuit breaker: fail fast while open, and report the
        # outcome of every real upstream attempt.
        check_circuit_breaker(model_name)
        try:
            response = await run_blocking(
                _call_api,
                timeout=_env_timeout("UNIGROK_API_CALL_TIMEOUT", 180.0),
            )
        except Exception:
            record_xai_failure(model_name)
            raise
        record_xai_success(model_name)
        prompt_tokens = 0
        completion_tokens = 0
        cost = 0.0
        if response.usage:
            prompt_tokens = getattr(response.usage, "prompt_tokens", 0)
            completion_tokens = getattr(response.usage, "completion_tokens", 0)
        if hasattr(response, "cost_usd") and response.cost_usd is not None:
            cost = response.cost_usd
        return response.content, prompt_tokens + completion_tokens, cost, False


async def _build_task_memory_context(active_store: Any, prompt: str, context_id: Optional[str]) -> str:
    if not active_store:
        return ""
    try:
        memories = await active_store.get_similar_task_memories(prompt, context_id=context_id, limit=3)
        return format_task_memory_notes(memories)
    except Exception as exc:
        logging.getLogger("GrokMCP").warning(f"Task memory retrieval failed: {exc}")
        return ""


async def _save_task_memory_safe(
    active_store: Any,
    prompt: str,
    layer: MetaLayer,
    model: str,
    success: int,
):
    if not active_store:
        return
    try:
        # Escalation outcomes ride task-memory metadata so future retrieval
        # sees which tasks needed the planning model.
        task_metadata = {"escalated": True} if layer.escalated else None
        await active_store.save_task_memory(
            prompt=prompt,
            outcome_summary=layer.generation or layer.reflection or layer.reasoning or "",
            plane=layer.plane,
            model=model,
            profile=layer.profile,
            success=success,
            latency=layer.latency,
            cost=layer.cost_usd,
            context_id=layer.context_id,
            metadata=task_metadata,
        )
        # Best-effort cloud mirror (UNIGROK_TASK_RAG mirror|shadow|active):
        # a single-flight fire-and-forget outbox drain — never blocks or
        # fails the response path. Late import: utils must not import rag
        # at module top (rag imports utils).
        from .rag import spawn_sync_task

        spawn_sync_task(active_store)
    except Exception as exc:
        logging.getLogger("GrokMCP").warning(f"Task memory save failed: {exc}")


# ─── Unified Orchestrate — Thin Router (v2) ──────────────────────────────────
@_with_request_id
async def orchestrate(
    prompt: str,
    session: Optional[str] = None,
    mode: Literal["auto", "reasoning", "composer"] = "auto",
    thinking_mode: bool = False,
    store: Any = None,
    dynamic_sys_prompt: str = "",
    requested_model: Optional[str] = None,
    mcp_instance: Any = None,
    enable_agentic: bool = True,
    context_id: Optional[str] = None,
    agent_count: Optional[int] = None,
    input_messages: Optional[List[Dict[str, Any]]] = None,
    on_event: Optional[Callable] = None,
    include: Optional[List[str]] = None,
    caller: Optional[str] = None,
    require_reasoning_level: Optional[Literal["low", "medium", "high"]] = None,
) -> MetaLayer:
    """
    Route a prompt through the layered execution planes:
      - Thinking route (run_thinking_loop): only when thinking_mode=True —
        AgentLoop execution wrapped in a schema-enforced reflection loop
        (chat.parse(ReflectionVerdict) reviews the answer; failing verdicts
        trigger bounded retries under one shared budget).
      - AgentLoop (ReAct): the DEFAULT path. The full tool surface is attached
        on every request and the model self-directs whether to act. The local
        keyword heuristic no longer gates the agent — it only selects the model
        (reasoning-scored prompts → planning model, others → coding model) when
        the caller has not pinned one.
      - Fast path (_call_plane): toolless single call. Used only when
        enable_agentic=False, when UNIGROK_FORCE_FAST is truthy (kill-switch),
        or as the fallback when the intelligence routes above raise.
    """
    start_time = time.time()
    session = scoped_session(session)

    # Caller identity: explicit param wins, else whatever the transport bound
    # to the current async context (HTTP gateway middleware / MCP clientInfo
    # capture in the tool layer). May legitimately stay None.
    caller = normalize_caller(caller) or get_active_caller()
    # Pre-execution budget gate — raises CallerBudgetExceeded (surfaced as a
    # clean tool error) BEFORE any model work when the caller's daily
    # UNIGROK_CALLER_BUDGETS spend is at/over its limit; a no-op when the env
    # is unset or the caller matches no entry.
    await enforce_caller_budget(store, caller)

    # Inject tool manifest into system prompt if MCP instance available
    if mcp_instance:
        subagents_manifest = await get_tools_manifest(mcp_instance)
        if subagents_manifest:
            dynamic_sys_prompt += f"\n\n{subagents_manifest}"

    reason_score = routing_reason_score(prompt) if mode == "auto" else 0
    route_uses_reasoning = (
        thinking_mode
        or mode == "reasoning"
        or (mode == "auto" and reason_score >= 2)
    )
    if (
        not route_uses_reasoning
        and mode == "auto"
        and reason_score == 1
        and requested_model is None
    ):
        # Borderline score (exactly 1) statically falls to the coding model.
        # The telemetry-informed prior flips it to planning only when recent
        # success rates justify it (see RoutingAdvisor); any advisor failure
        # keeps the static prior.
        try:
            route_uses_reasoning = await get_routing_advisor().prefers_planning(
                store,
                await resolve_model("planning"),
                await resolve_model("coding"),
                prompt=prompt,
                context_id=context_id,
            )
        except Exception as advisor_err:
            logging.getLogger("GrokMCP").warning(
                f"Routing advisor unavailable; keeping static prior: {advisor_err}"
            )
    # Alias resolution: in a local/Docker prototype with no xAI API key but a
    # mounted Grok CLI, use the subscription CLI plane directly. Do not try the
    # API first and then explain auth errors to the browser UI.
    keyless_cli_direct = prefer_cli_when_api_key_missing()
    if keyless_cli_direct:
        profile_model = keyless_cli_model(requested_model, route_uses_reasoning)
        if thinking_mode:
            logging.getLogger("GrokMCP").info(
                "thinking_mode requires the xAI API plane; using direct Grok CLI route because XAI_API_KEY is not configured."
            )
        thinking_mode = False
    else:
        # Reasoning-scored prompts ride the planning alias, everything else
        # the coding alias. resolve_model consults the live catalog
        # (TTL-cached) and degrades to the static defaults when discovery is
        # unavailable, disabled, or under UNI_GROK_TESTING. Explicit
        # requested_model slugs are never rewritten.
        profile_model = requested_model or await resolve_model(
            "planning" if route_uses_reasoning else "coding"
        )
    routing_why = (
        "pin"
        if requested_model or mode != "auto" or thinking_mode
        else "cost"
        if keyless_cli_direct or not route_uses_reasoning
        else "auto"
    )
    active_profile = load_grok_profile(profile_model)
    if require_reasoning_level:
        level_map = {"none": 0, "low": 1, "medium": 2, "high": 3}
        required_val = level_map.get(require_reasoning_level.lower(), 0)
        chosen_effort = active_profile.get("reasoning_effort") or "none"
        chosen_val = level_map.get(chosen_effort.lower(), 0)
        if chosen_val < required_val:
            raise ValueError(
                f"Model '{profile_model}' reasoning effort '{chosen_effort}' "
                f"does not satisfy required reasoning level '{require_reasoning_level}'."
            )
    memory_notes = await _build_task_memory_context(store, prompt, context_id)
    adapter_prompt = load_grok_prompt(str(active_profile.get("system_prompt_ref") or ""))
    workspace_context, caller_instructions = _split_caller_instructions(dynamic_sys_prompt)
    dynamic_sys_prompt = compose_system_prompt(
        workspace_context,
        adapter_prompt=adapter_prompt,
        memory_notes=memory_notes,
        caller_instructions=caller_instructions,
    )

    # ── AGENTIC PATH: True ReAct loop (default) ───────────────────────────────
    # AgentLoop is the default execution path — the model gets its full tool
    # surface on every request and decides for itself whether to act. The
    # keyword heuristic (route_uses_reasoning) only selects the model above;
    # it no longer gates agent access. The fast path remains only as:
    #   1. the route when enable_agentic=False,
    #   2. the route when UNIGROK_FORCE_FAST is truthy (env kill-switch),
    #   3. the fallback when AgentLoop raises.
    force_fast = os.environ.get("UNIGROK_FORCE_FAST", "").strip().lower() in ("1", "true", "yes")
    use_agentic = enable_agentic and not force_fast and not keyless_cli_direct
    # Track intelligence-route failovers so the fast-path outcome stays honest.
    degraded_route = False

    if thinking_mode:
        try:
            # thinking_mode forces route_uses_reasoning, so profile_model is
            # already the requested slug or the resolved planning alias.
            actual_model = profile_model
            layer = await run_thinking_loop(
                prompt,
                session=session,
                store=store,
                dynamic_sys_prompt=dynamic_sys_prompt,
                model=actual_model,
                context_id=context_id,
                profile=active_profile,
                input_messages=input_messages,
                on_event=on_event,
                caller=caller,
            )
            layer.latency = time.time() - start_time
            layer.context_id = context_id
            layer.route = "thinking"
            layer.model = actual_model
            layer.profile = str(active_profile.get("profile") or "")
            layer.policy_mode = current_policy_mode()
            layer.routing_why = routing_why
            layer.degraded = False
            success = 1 if layer.finish_reason == "final_answer" else 0
            await _save_task_memory_safe(store, prompt, layer, actual_model, success)
            return layer
        except Exception as e:
            logging.getLogger("GrokMCP").warning(
                f"Thinking route failed, falling back: {e}"
            )
            degraded_route = True

    if use_agentic:
        try:
            actual_model = profile_model
            policy = AgentLoopPolicy()
            loop = AgentLoop(
                policy=policy,
                dynamic_sys_prompt=dynamic_sys_prompt,
                model=actual_model,
                store=store,
                agent_count=agent_count,
                profile=active_profile,
                on_event=on_event,
                include=include,
            )
            history = (await load_history(session)) if session and not input_messages else None
            layer = await loop.run(prompt, session, history=history, input_messages=input_messages)
            layer.latency = time.time() - start_time
            layer.context_id = context_id
            layer.route = "agentic"
            layer.model = actual_model
            layer.profile = str(active_profile.get("profile") or layer.profile or "")
            layer.policy_mode = current_policy_mode()
            layer.routing_why = routing_why
            layer.degraded = False
            success = 1 if layer.finish_reason == "final_answer" else 0
            if store:
                await store.save_telemetry(prompt[:100], layer.plane, success, layer.latency, layer.cost_usd, context_id=context_id, caller=caller)
            await _save_task_memory_safe(store, prompt, layer, actual_model, success)
            return layer
        except Exception as e:
            logging.getLogger("GrokMCP").warning(
                f"AgentLoop failed, falling back to fast path: {e}"
            )
            degraded_route = True
            # Fall through to fast path below

    # ── FAST PATH: Single toolless call ───────────────────────────────────────
    # Reached only when agentic execution is disabled/kill-switched or the
    # intelligence routes above raised. Same alias resolution as above:
    # profile_model is the requested slug or the resolved planning/coding alias.
    actual_model = profile_model
    active_profile = load_grok_profile(actual_model)
    actual_mode: Literal["reasoning", "composer"] = "reasoning"
    cli_max_turns = AgentLoopPolicy().max_depth if enable_agentic and not force_fast else None

    layer = MetaLayer()
    try:
        gen_res, g_tok, g_cost, is_cli = await _call_plane(
            actual_mode, prompt, session, store, dynamic_sys_prompt,
            requested_model=actual_model,
            agent_count=agent_count,
            input_messages=input_messages,
            profile=active_profile,
            on_event=on_event,
            include=include,
            max_turns=cli_max_turns,
        )
        layer.generation = gen_res
        layer.tokens = g_tok
        layer.cost_usd = g_cost
        layer.plane = "CLI" if is_cli else "API"
        layer.route = "fast"
        layer.model = actual_model
        layer.finish_reason = "fallback" if degraded_route else "final_answer"
        layer.profile = str(active_profile.get("profile") or "")
        layer.policy_mode = current_policy_mode()
        layer.context_id = context_id
        layer.routing_why = "failover" if degraded_route else routing_why
        layer.degraded = degraded_route
        if store:
            await store.save_telemetry(prompt[:100], layer.plane, 1, time.time() - start_time, g_cost, context_id=context_id, caller=caller)

    except Exception as e:
        if is_cli_model(actual_model):
            logging.getLogger("GrokMCP").warning(f"Direct Grok CLI route failed: {e}")
            layer.generation = str(e)
            layer.tokens = 0
            layer.cost_usd = 0.0
            layer.plane = "CLI"
            layer.route = "fast"
            layer.model = actual_model
            layer.finish_reason = "error"
            layer.profile = str(active_profile.get("profile") or "")
            layer.policy_mode = current_policy_mode()
            layer.context_id = context_id
            layer.routing_why = "failover" if degraded_route else routing_why
            layer.degraded = degraded_route
            if store:
                await store.save_telemetry(
                    prompt[:100],
                    layer.plane,
                    0,
                    time.time() - start_time,
                    layer.cost_usd,
                    context_id=context_id,
                    caller=caller,
                )
            layer.latency = time.time() - start_time
            await _save_task_memory_safe(store, prompt, layer, actual_model, 0)
            return layer

        # Graceful CLI fallback
        if is_cloudrun_runtime():
            if store:
                await store.save_telemetry(prompt[:100], "API", 0, time.time() - start_time, layer.cost_usd, context_id=context_id, caller=caller)
            raise RuntimeError(f"xAI API execution failed in Cloud Run runtime: {e}") from e

        logging.getLogger("GrokMCP").warning(
            f"Fast path failed, activating local CLI fallback: {e}"
        )
        layer.fallback_occurred = True
        layer.degraded = True
        layer.routing_why = "failover"
        layer.plane = "CLI-Fallback"
        try:
            fallback_profile = load_grok_profile("grok-composer-2.5-fast")
            gen_res, g_tok, g_cost, _ = await _call_plane(
                "cli-fallback", prompt, session, store, dynamic_sys_prompt,
                requested_model="grok-composer-2.5-fast",
                profile=fallback_profile,
                max_turns=cli_max_turns,
            )
            layer.generation = gen_res
            layer.tokens = g_tok
            layer.cost_usd = g_cost
            layer.context_id = context_id
            layer.route = "cli-fallback"
            layer.model = "grok-composer-2.5-fast"
            layer.profile = str(fallback_profile.get("profile") or "")
            layer.policy_mode = current_policy_mode()
            layer.finish_reason = "fallback"
            layer.degraded = True
            layer.routing_why = "failover"
            if store:
                await store.save_telemetry(prompt[:100], layer.plane, 1, time.time() - start_time, g_cost, context_id=context_id, caller=caller)
        except Exception as cli_err:
            # Keep the original API-plane failure visible: on an API-only host
            # (no grok binary) the FileNotFoundError alone would mask the real
            # cause the caller needs to act on.
            layer.generation = f"CLI recovery failed: {cli_err} (original API-plane error: {e})"
            layer.context_id = context_id
            layer.route = "cli-fallback"
            layer.model = "grok-composer-2.5-fast"
            layer.profile = str(active_profile.get("profile") or "")
            layer.policy_mode = current_policy_mode()
            layer.finish_reason = "error"
            layer.degraded = True
            layer.routing_why = "failover"
            if store:
                await store.save_telemetry(prompt[:100], layer.plane, 0, time.time() - start_time, layer.cost_usd, context_id=context_id, caller=caller)

    layer.latency = time.time() - start_time
    await _save_task_memory_safe(store, prompt, layer, actual_model, 1 if layer.finish_reason == "final_answer" else 0)
    if layer.finish_reason == "fallback":
        logging.getLogger("GrokMCP").warning(
            f"Grok MCP Router: fallback finish reason triggered (degraded run/typo rerouted). Actual model: {actual_model}, route: {layer.route}"
        )
    return layer


@_with_request_id
async def run_agent_turn(
    prompt: Optional[str] = None,
    session: Optional[str] = None,
    system_prompt: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    mode: str = "auto",
    thinking_mode: bool = False,
    enable_agentic: bool = True,
    on_event: Optional[Callable] = None,
    agent_count: Optional[int] = None,
    include: Optional[List[str]] = None,
    caller: Optional[str] = None,
    require_reasoning_level: Optional[Literal["low", "medium", "high"]] = None,
) -> MetaLayer:
    """Shared single-agent gateway boundary used by HTTP and remote MCP.

    model=None lets orchestrate() auto-select between the planning and coding
    defaults; mode, thinking_mode, and enable_agentic pass straight through to
    orchestrate() (enable_agentic=False selects the toolless fast path).
    agent_count (4|16 multi-agent fan-out) and include (extra response
    surfaces such as ["inline_citations"]) forward to orchestrate() for the
    agent tool's research mode — both are capability-gated downstream.
    on_event (sync or async) receives progress events — depth advances, tool
    start/end, and real content deltas on the fast plane (see
    _emit_agent_event for the event shapes).
    caller is the calling agent's identity (MCP clientInfo name or the HTTP
    gateway's X-Caller/auth-key alias); None falls back to whatever the
    transport bound to the current async context, and it flows into telemetry
    attribution, per-caller budgets, and session message metadata.
    """
    caller = normalize_caller(caller) or get_active_caller()
    session = scoped_session(session)
    final_prompt = prompt or ""
    system_parts = []
    input_messages: List[Dict[str, Any]] = []

    if messages:
        for message in messages:
            role = str(message.get("role", "")).lower()
            content = _message_content_to_text(message.get("content", ""))
            if role == "system" and content:
                system_parts.append(content)
            elif content:
                normalized = {
                    "role": role if role in ("user", "assistant", "tool") else "user",
                    "content": message.get("content", ""),
                }
                for key in ("name", "tool_call_id", "id"):
                    if message.get(key):
                        normalized[key] = message[key]
                input_messages.append(normalized)

        user_indexes = [idx for idx, message in enumerate(input_messages) if message["role"] == "user"]
        if user_indexes:
            final_prompt = _message_content_to_text(input_messages[user_indexes[-1]].get("content", ""))
        elif input_messages:
            final_prompt = final_prompt or _message_content_to_text(input_messages[-1].get("content", "")) or "Continue the conversation."

    if system_prompt:
        system_parts.append(system_prompt)

    if not final_prompt:
        raise ValueError("A prompt or at least one user message is required.")

    dynamic_sys_prompt, _, context_id = await get_dynamic_context(prompt=final_prompt)
    if system_parts:
        dynamic_sys_prompt += "\nAdditional Instructions:\n" + "\n\n".join(system_parts)

    layer = await orchestrate(
        prompt=final_prompt,
        session=session,
        mode=mode,
        thinking_mode=thinking_mode,
        store=store,
        dynamic_sys_prompt=dynamic_sys_prompt,
        requested_model=model,
        enable_agentic=enable_agentic,
        context_id=context_id,
        agent_count=agent_count,
        input_messages=input_messages or None,
        on_event=on_event,
        include=include,
        caller=caller,
        require_reasoning_level=require_reasoning_level,
    )

    if session and layer.generation:
        history = await load_history(session, store)
        metadata = {"model": model, "plane": layer.plane, "context_id": context_id, "tokens": layer.tokens, "cost": layer.cost_usd}
        metadata["routing_why"] = layer.routing_why
        metadata["degraded"] = layer.degraded
        if caller:
            # Which agent drove this turn — session transcripts in a shared
            # multi-agent workspace need per-turn attribution.
            metadata["caller"] = caller
        if layer.tool_trace:
            # Persist tool observations so the next turn's AgentLoop can
            # replay them and continue multi-step work.
            metadata["tool_trace"] = layer.tool_trace
        if layer.response_id:
            metadata["response_id"] = layer.response_id
        if layer.escalated:
            # The turn self-escalated from the coding to the planning model.
            metadata["escalated"] = True
        await append_and_save_history(
            session,
            history,
            final_prompt,
            layer.generation,
            store,
            metadata=metadata,
        )
        # api_thread_id carries the server-side thread head: the stored
        # completion id when server state was active, else the legacy session
        # placeholder (which the loop knows never to send upstream).
        await store.save_session(session, api_thread_id=layer.response_id or session, model=model)
        try:
            await maybe_compact_history(session, history, store)
        except Exception as compact_err:
            logging.getLogger("GrokMCP").warning(
                f"History compaction skipped for session '{session}': {compact_err}"
            )

    return layer


def _image_part_to_text(part: Dict[str, Any]) -> str:
    image_value = part.get("image_url") or part.get("url")
    if isinstance(image_value, dict):
        image_value = image_value.get("url")
    return f"[image: {image_value}]" if image_value else "[image]"


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                part_type = str(part.get("type", "")).lower()
                if "image_url" in part or part_type in ("image_url", "input_image"):
                    parts.append(_image_part_to_text(part))
                elif "text" in part:
                    parts.append(str(part["text"]))
                elif "content" in part:
                    parts.append(str(part["content"]))
                else:
                    parts.append(json.dumps(part, separators=(",", ":"), ensure_ascii=False))
            else:
                parts.append(str(part))
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict):
        part_type = str(content.get("type", "")).lower()
        if "image_url" in content or part_type in ("image_url", "input_image"):
            return _image_part_to_text(content)
        if "text" in content:
            return str(content["text"]).strip()
        if "content" in content:
            return str(content["content"]).strip()
        return json.dumps(content, separators=(",", ":"), ensure_ascii=False).strip()
    return str(content or "").strip()


def _build_custom_tools(include_escalation: bool = False) -> list:
    try:
        from xai_sdk.chat import tool as sdk_tool

        custom_tools = []
        allow_local_tools = not is_cloudrun_runtime()
        allow_git_write = get_unigrok_runtime() == "local" and os.environ.get("ENABLE_GIT_WRITE") == "1"

        # 0. escalate_reasoning — loop-bound self-escalation. Only offered
        # when the calling AgentLoop started on the coding model.
        if include_escalation:
            custom_tools.append(sdk_tool(
                name=_ESCALATE_TOOL_NAME,
                description=_ESCALATE_TOOL_DESCRIPTION,
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Why the task needs deeper reasoning."}
                    },
                    "required": ["reason"]
                }
            ))

        # 1. Curated UniGrok FAQ: an agent-controlled lookup, deliberately
        # not a keyword-triggered response mechanism or public MCP command.
        custom_tools.append(sdk_tool(
            name="lookup_unigrok_faq",
            description=(
                "Look up verified UniGrok support context. Call only when the user explicitly "
                "asks about UniGrok configuration, IDE setup, routing, security, health, or "
                "troubleshooting. Do not use it for unrelated questions that merely share words "
                "like 'Cursor', 'port', or 'API key'. If there is no match, answer normally."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The user's UniGrok-specific support question.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum matching entries to inspect (1-10, default 3).",
                    },
                },
                "required": ["query"],
            },
        ))

        # 2. generate_image
        custom_tools.append(sdk_tool(
            name="generate_image",
            description="Generate a new image or edit an existing one based on a text prompt.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The image description or edit prompt."},
                    "n": {"type": "integer", "description": "Number of images to generate (1-10)."},
                    "aspect_ratio": {"type": "string", "description": "Aspect ratio, e.g. '1:1', '16:9', '9:16'."}
                },
                "required": ["prompt"]
            }
        ))

        # 2. upload_file
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="upload_file",
                description="Upload a local project file to xAI so it can be attached to later chats.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to the local file to upload."}
                    },
                    "required": ["file_path"]
                }
            ))

        # 3. get_file_content
        custom_tools.append(sdk_tool(
            name="get_file_content",
            description="Download the raw text content of an uploaded file from xAI using its file ID.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "The ID of the file to download."},
                    "max_bytes": {"type": "integer", "description": "Max bytes to download (default 4000)."}
                },
                "required": ["file_id"]
            }
        ))

        # 4. read_local_file
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="read_local_file",
                description="Read the contents of a local project file for code context or analysis.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "The relative path to the local file."},
                        "max_chars": {"type": "integer", "description": "Max characters to read (default 6000)."}
                    },
                    "required": ["file_path"]
                }
            ))

        # 5. list_project_files
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="list_project_files",
                description="List source code and text files present in the current workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "extensions": {"type": "string", "description": "Comma-separated extensions to filter (e.g. 'py,js,ts')."}
                    }
                }
            ))

        # 6. get_session_history
        custom_tools.append(sdk_tool(
            name="get_session_history",
            description="Retrieve local conversation history for a specific session.",
            parameters={
                "type": "object",
                "properties": {
                    "session": {"type": "string", "description": "The name of the chat session."}
                },
                "required": ["session"]
            }
        ))

        # 7. git_status
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="git_status",
                description="Return concise git working-tree status for the local project.",
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    }
                }
            ))

        # 8. git_diff
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="git_diff",
                description="Return the current git diff, optionally staged or limited to one path.",
                parameters={
                    "type": "object",
                    "properties": {
                        "cached": {"type": "boolean", "description": "Use true to inspect staged changes."},
                        "path": {"type": "string", "description": "Optional relative path inside the repository."},
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    }
                }
            ))

        # 9. git_log
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="git_log",
                description="Return a bounded one-line git history.",
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of commits to show, clamped to 1-100."},
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    }
                }
            ))

        # 10. git_show
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="git_show",
                description="Return git show output for a validated commit-ish ref.",
                parameters={
                    "type": "object",
                    "properties": {
                        "commit": {"type": "string", "description": "Commit-ish ref to inspect, default HEAD."},
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    }
                }
            ))

        # 11. git_current_branch
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="git_current_branch",
                description="Return the active git branch name.",
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    }
                }
            ))

        # 12. git_apply_patch
        if allow_git_write:
            custom_tools.append(sdk_tool(
                name="git_apply_patch",
                description="Apply a validated unified diff to the local repository. Requires local git write mode.",
                parameters={
                    "type": "object",
                    "properties": {
                        "patch": {"type": "string", "description": "Unified diff patch to apply."},
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    },
                    "required": ["patch"]
                }
            ))

        # 13. git_commit
        if allow_git_write:
            custom_tools.append(sdk_tool(
                name="git_commit",
                description="Commit explicit paths in the local repository. Requires local git write mode.",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Commit message."},
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Explicit relative paths to stage and commit."
                        },
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    },
                    "required": ["message", "paths"]
                }
            ))

        # 14. git_create_branch
        if allow_git_write:
            custom_tools.append(sdk_tool(
                name="git_create_branch",
                description="Create and switch to a new local branch. Requires local git write mode.",
                parameters={
                    "type": "object",
                    "properties": {
                        "branch_name": {"type": "string", "description": "New branch name."},
                        "repo_path": {"type": "string", "description": "Optional repository path inside the project root."}
                    },
                    "required": ["branch_name"]
                }
            ))

        # 15. run_local_tests
        if allow_local_tools:
            custom_tools.append(sdk_tool(
                name="run_local_tests",
                description="Run the local pytest suite or a validated test path without shell access.",
                parameters={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Test target path or 'all'. Defaults to tests."},
                        "max_seconds": {"type": "integer", "description": "Timeout in seconds, clamped to 5-300."},
                        "max_output_chars": {"type": "integer", "description": "Maximum combined output characters, clamped to 1000-50000."}
                    }
                }
            ))

        return custom_tools
    except Exception as e:
        logging.getLogger("GrokMCP").warning(f"Could not build custom tool schemas: {e}")
        return []

def load_gitignore_patterns(project_root: Path) -> List[str]:
    gitignore_path = project_root / ".gitignore"
    patterns = []
    if gitignore_path.exists():
        try:
            for line in gitignore_path.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        except Exception:
            pass
    return patterns

def is_path_ignored(path: Path, project_root: Path, gitignore_patterns: List[str]) -> bool:
    standard_ignored_dirs = {
        'node_modules', '.venv', 'venv', 'env', 'chats', 'logs',
        'build', 'dist', 'out', 'target', 'bin', 'obj', '.git',
        '.github', '.pytest_cache', '__pycache__', 'uv.lock'
    }
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return True # Outside project root is ignored by definition
    for part in relative.parts:
        if part in standard_ignored_dirs:
            return True

    import fnmatch
    rel_str = str(relative)
    for pattern in gitignore_patterns:
        pat = pattern.rstrip('/')
        if fnmatch.fnmatch(rel_str, pat) or fnmatch.fnmatch(rel_str, f"*/{pat}") or any(fnmatch.fnmatch(part, pat) for part in relative.parts):
            return True
    return False
