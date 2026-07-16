from ..utils import create_scrubbed_subprocess_exec
# src/tools/system.py
# Decomposed System, Files, and Diagnostics tools for UniGrok MCP

import logging
import asyncio
import json
import os
import subprocess
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List, Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from ..models.results import SystemResult
from ..metrics import build_metrics_snapshot, fetch_provider_api_usage
from ..semantic_evals import get_semantic_eval_stats
from ..version import __version__

from ..utils import (
    CLI_AUTH_SETUP_COMMAND,
    store,
    get_xai_client,
    GrokInvocationContext,
    PathResolver,
    is_path_ignored,
    load_gitignore_patterns,
    XAI_API_KEY,
    is_cloudrun_runtime,
    inspect_grok_adapter,
    build_model_catalog,
    discover_xai_api_models,
    register_internal_tool,
    run_blocking,
    communicate_with_timeout,
    get_runtime_stats,
    get_circuit_breaker_state,
    get_routing_advisor,
    grok_cli_plane_status,
    credential_plane_contract,
    input_limit,
    validate_local_input,
)
from xai_sdk.chat import user
from xai_sdk.tools import code_execution, web_search as xai_web_search, x_search as xai_x_search

from ..identity import get_active_client_id, principal_kind, scoped_session

logger = logging.getLogger("GrokMCP")

# Real MCP spec tool annotations, passed to mcp.add_tool(annotations=...):
# readOnlyHint marks tools that never modify their environment;
# destructiveHint marks tools whose mutations are not trivially reversible.
READONLY_TOOL = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE_TOOL = ToolAnnotations(destructiveHint=True)
_ALLOWED_FINISH_REASONS = {"final_answer", "fallback", "tool_calls", "length", "unknown", "error"}


def _normalize_finish_reason(response: Any) -> str:
    finish_reason = getattr(response, "finish_reason", "final_answer") or "final_answer"
    return finish_reason if finish_reason in _ALLOWED_FINISH_REASONS else "unknown"


def _tokens_from_response(response: Any) -> int:
    usage = getattr(response, "usage", None)
    if not usage:
        return 0
    return int((getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0))


def _build_tool_result(
    ctx: GrokInvocationContext,
    *,
    response: Any,
    route: str,
    model: str,
    formatted_text: str,
    data: Optional[Dict[str, Any]] = None,
    citations: Optional[List[Dict[str, str]]] = None,
) -> SystemResult:
    return SystemResult(
        response=response.content,
        text=formatted_text,
        finish_reason=_normalize_finish_reason(response),
        cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
        model=model,
        tokens=_tokens_from_response(response),
        latency_sec=ctx.elapsed,
        route=route,
        plane="API",
        citations=citations,
        data=data,
    )


def _resolve_workspace_file(
    file_path: str,
    *,
    enforce_ignore_policy: bool,
) -> Path:
    proj_root = PathResolver.get_workspace_root()
    if proj_root is None:
        raise RuntimeError("No workspace is attached to this UniGrok service.")
    resolved = PathResolver.validate_path(file_path)
    if enforce_ignore_policy:
        patterns = load_gitignore_patterns(proj_root)
        if is_path_ignored(resolved, proj_root, patterns):
            raise PermissionError(f"path '{file_path}' is ignored or private.")
    return resolved


async def grok_mcp_status(view: Literal["text", "json"] = "text") -> str:
    """Inspect health and usage; ``view=json`` returns stable structured metrics."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        server_version = __version__
        proj_root = PathResolver.get_service_root()
        grok_cli = PathResolver.get_grok_cli_path()
        uv_bin = PathResolver.get_uv_path()

        # Verify the service-level OAuth plane without inheriting XAI_API_KEY.
        try:
            cli_plane = await run_blocking(
                grok_cli_plane_status,
                timeout_sec=5.0,
                timeout=6.0,
            )
        except Exception:
            cli_plane = {
                "state": "unreachable",
                "auth": "probe_failed",
                "setup_command": CLI_AUTH_SETUP_COMMAND,
            }
        cli_auth = f"{cli_plane['state']} ({cli_plane['auth']})"
        credential_planes = credential_plane_contract(cli_plane)

        git_sha = "Unknown"
        try:
            proc_git = await create_scrubbed_subprocess_exec(
                "git", "rev-parse", "HEAD",
                cwd=str(proj_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout_git, _ = await communicate_with_timeout(proc_git, 3.0)
            if proc_git.returncode == 0:
                git_sha = stdout_git.decode().strip()
        except Exception:
            pass

        sessions = await store.list_sessions()

        # Compute SQLite File Sizes
        db_size_kb = 0
        wal_size_kb = 0
        try:
            db_size_kb = store.db_path.stat().st_size / 1024
            wal_path = Path(str(store.db_path) + "-wal")
            if wal_path.exists():
                wal_size_kb = wal_path.stat().st_size / 1024
        except Exception:
            pass

        # Query Database Counts and Health
        total_msgs = 0
        total_telemetry = 0
        total_task_memory = 0
        integrity = "Unknown"
        schema_ver = 0
        adapter_status = inspect_grok_adapter()
        try:
            await store._ensure_initialized()
            async with store._lock:
                async with store._conn.execute("SELECT COUNT(*) FROM messages") as cursor:
                    row = await cursor.fetchone()
                    total_msgs = row[0] if row else 0
                async with store._conn.execute("SELECT COUNT(*) FROM telemetry") as cursor:
                    row = await cursor.fetchone()
                    total_telemetry = row[0] if row else 0
                async with store._conn.execute("SELECT COUNT(*) FROM task_memory") as cursor:
                    row = await cursor.fetchone()
                    total_task_memory = row[0] if row else 0
                async with store._conn.execute("PRAGMA user_version;") as cursor:
                    row = await cursor.fetchone()
                    schema_ver = row[0] if row else 0
                async with store._conn.execute("PRAGMA integrity_check;") as cursor:
                    row = await cursor.fetchone()
                    integrity = row[0] if row else "Unknown"
        except Exception as e:
            logger.warning(f"Failed to query status DB counts: {e}")

        # Compute Telemetry Aggregates
        avg_latency = 0.0
        p95_latency = 0.0
        total_cost = 0.0
        cli_count = 0
        api_count = 0
        success_rate: Optional[float] = None
        verified_outcomes = 0
        unverified_requests = 0
        latest_context_id = "none"

        stats: List[Dict[str, Any]] = []
        try:
            stats = await store.get_telemetry_stats()
            if stats:
                task_stats = [
                    s
                    for s in stats
                    if str(s.get("intent") or "") != "history-compaction"
                ]
                latencies = sorted([
                    s["latency"] for s in task_stats if s.get("latency") is not None
                ])
                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
                    p95_idx = int(len(latencies) * 0.95)
                    p95_latency = latencies[min(p95_idx, len(latencies) - 1)]

                total_cost = sum([s["cost"] for s in stats if s.get("cost") is not None])
                cli_count = sum(1 for s in task_stats if s.get("chosen_plane", "").lower().startswith("cli"))
                api_count = sum(1 for s in task_stats if s.get("chosen_plane", "").lower() == "api")
                verified_stats = [s for s in task_stats if s.get("success") in (0, 1)]
                verified_outcomes = len(verified_stats)
                unverified_requests = len(task_stats) - verified_outcomes
                successes = sum(1 for s in verified_stats if s.get("success") == 1)
                if verified_stats:
                    success_rate = (successes / len(verified_stats)) * 100.0
                latest_context_id = next((s.get("context_id") for s in stats if s.get("context_id")), "none")
        except Exception:
            pass

        # Per-caller attribution: today's busiest connected agents (telemetry
        # metadata carries the caller name since schema v8; unattributed
        # traffic and old rows simply don't show up here).
        top_callers = "`none`"
        try:
            caller_rows = await store.get_caller_stats_today(limit=5)
            if caller_rows:
                caller_parts = []
                for row in caller_rows:
                    caller_rate = row.get("success_rate")
                    rate_text = (
                        f"{caller_rate * 100:.0f}% success"
                        if caller_rate is not None
                        else "success unverified"
                    )
                    caller_verified = int(
                        row.get("verified_outcomes")
                        if row.get("verified_outcomes") is not None
                        else row["requests"] if caller_rate is not None else 0
                    )
                    caller_parts.append(
                        f"`{row['caller']}`: {row['requests']} reqs, "
                        f"{rate_text} ({caller_verified} verified), "
                        f"${row['total_cost_usd']:.4f}"
                    )
                top_callers = "; ".join(caller_parts)
        except Exception:
            pass

        # Runtime concurrency: timed-thread pressure + per-model breaker state
        runtime = get_runtime_stats()
        breakers = get_circuit_breaker_state()
        if breakers:
            breaker_summary = "; ".join(
                f"`{model}`: {'OPEN' if state['open'] else 'closed'} "
                f"(failures={state['consecutive_failures']}, trips={state['trips']}"
                + (f", cooldown={state['cooldown_remaining_sec']}s" if state["open"] else "")
                + ")"
                for model, state in sorted(breakers.items())
            )
        else:
            breaker_summary = "`none tracked`"

        # Telemetry-informed borderline routing prior (RoutingAdvisor view)
        advisor_summary = "`unavailable`"
        advisor_view: Optional[Dict[str, Any]] = None
        try:
            advisor_view = await get_routing_advisor().status_view(store)
            planning_view = advisor_view["planning"]
            coding_view = advisor_view["coding"]
            advisor_summary = (
                f"borderline → `{advisor_view['borderline_choice']}` — "
                f"planning `{advisor_view['planning_model']}`: {planning_view['samples']} samples, "
                f"{planning_view['success_rate'] * 100:.0f}% success, ${planning_view['avg_cost']:.4f} avg; "
                f"coding `{advisor_view['coding_model']}`: {coding_view['samples']} samples, "
                f"{coding_view['success_rate'] * 100:.0f}% success, ${coding_view['avg_cost']:.4f} avg "
                f"(margin {advisor_view['margin']:.2f}, min {advisor_view['min_samples']} samples/model)"
            )
        except Exception:
            pass

        # Shadow semantic-eval judge (observational only; off by default)
        semantic_summary = "`off`"
        semantic_stats: Optional[Dict[str, Any]] = None
        try:
            semantic_stats = get_semantic_eval_stats()
            if semantic_stats["mode"] == "shadow":
                avg_scores = semantic_stats.get("avg_scores") or {}
                overall = avg_scores.get("overall")
                semantic_summary = (
                    f"`shadow` (rate {semantic_stats['rate']:.2f}) — "
                    f"graded {semantic_stats['graded']} this process, avg overall "
                    + (f"`{overall:.2f}/5`" if overall is not None else "`n/a`")
                )
        except Exception:
            pass

        if view == "json":
            provider_api = await fetch_provider_api_usage()
            snapshot = build_metrics_snapshot(
                stats,
                runtime=runtime,
                circuit_breakers=breakers,
                routing_advisor=advisor_view,
                provider_api=provider_api,
                semantic_evals=semantic_stats,
            )
            snapshot["credential_planes"] = credential_planes
            return json.dumps(snapshot, separators=(",", ":"))

        success_rate_text = (
            f"{success_rate:.1f}%" if success_rate is not None else "unverified"
        )
        success_rate_text += (
            f" ({verified_outcomes} verified / "
            f"{unverified_requests} unverified)"
        )
        status_text = (
            "# UniGrok MCP Server Status\n\n"
            f"**Server Version:** `{server_version}`\n"
            f"**Git HEAD SHA:** `{git_sha}`\n"
            f"**Service Mode:** `{'contributor' if PathResolver.contributor_mode() else 'stable'}`\n"
            f"**Service Root:** `{proj_root}`\n"
            f"**Workspace Attached:** `{'yes' if PathResolver.get_workspace_root() is not None else 'no'}`\n"
            f"**Grok CLI Binary:** `{grok_cli}`\n"
            f"**UV Binary:** `{uv_bin}`\n"
            f"**CLI Authentication:** `{cli_auth}`\n"
            f"**CLI Auth Setup:** `{cli_plane['setup_command']}`\n"
            f"**Developer API Key:** `{'Configured' if XAI_API_KEY else 'Missing'}`\n\n"
            "### Credential Planes\n"
            f"- **Policy:** `{credential_planes['policy']}`\n"
            f"- **Preferred Plane:** `{credential_planes['preferred_plane']}`\n"
            f"- **Effective Plane:** `{credential_planes['effective_plane'] or 'none'}`\n"
            f"- **Service Usable:** `{'yes' if credential_planes['service_usable'] else 'no'}`\n"
            "- **Agent Rule:** ask before installation, device authentication, or secret configuration; "
            "never request `XAI_API_KEY` in chat or store it in a caller project.\n\n"
            "### .grok Adapter\n"
            f"- **Profile Files:** `{adapter_status['profile_count']}`\n"
            f"- **Prompt Files:** `{', '.join(adapter_status['prompts']) or 'none'}`\n"
            f"- **Profile Warnings:** `{'; '.join(adapter_status['warnings']) or 'none'}`\n\n"
            "### SQLite Storage Metrics\n"
            f"- **Database Path:** `{store.db_path}`\n"
            f"- **Database Size:** `{db_size_kb:.2f} KB`\n"
            f"- **WAL File Size:** `{wal_size_kb:.2f} KB`\n"
            f"- **Schema Version:** `{schema_ver}`\n"
            f"- **Database Integrity:** `{integrity}`\n\n"
            "### Database Row Counts\n"
            f"- **Active Database Sessions:** `{len(sessions)}`\n"
            f"- **Total Messages:** `{total_msgs}`\n"
            f"- **Telemetry Entries:** `{total_telemetry}`\n"
            f"- **Task Memory Entries:** `{total_task_memory}`\n\n"
            "### Runtime Concurrency\n"
            f"- **Timed Threads In Flight:** `{runtime['timed_threads_in_flight']}`\n"
            f"- **Timed Threads Peak:** `{runtime['timed_threads_peak']}`\n"
            f"- **Circuit Breakers:** {breaker_summary}\n"
            f"- **Routing Advisor:** {advisor_summary}\n\n"
            "### Execution Telemetry\n"
            f"- **Average Query Latency:** `{avg_latency:.2f}s`\n"
            f"- **P95 Query Latency:** `{p95_latency:.2f}s`\n"
            f"- **Total Cost (Developer API):** `${total_cost:.5f}`\n"
            f"- **CLI vs API Routing Split:** `{cli_count} CLI calls / {api_count} API calls`\n"
            f"- **Success Rate:** `{success_rate_text}`\n"
            f"- **Top Callers Today:** {top_callers}\n"
            f"- **Semantic Evals:** {semantic_summary}\n"
            f"- **Latest Context Snapshot:** `{latest_context_id}`\n"
        )
        return ctx.format_output(status_text)


async def list_chat_sessions() -> str:
    """List all chat sessions stored under the SQLite session store."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        sessions = await store.list_sessions()
        if not sessions:
            return ctx.format_output("No chat sessions found.")

        lines = ["# Stored Chat Sessions\n", "| Session Name | Active Model | Last Active Timestamp |", "| :--- | :--- | :--- |"]
        for s in sessions:
            model_str = s.get("model") or "unknown"
            lines.append(f"| `{s['session_name']}` | `{model_str}` | `{s['last_active']}` |")
        return ctx.format_output("\n".join(lines))


async def get_chat_history(session: str = "default", limit: int = 20) -> str:
    """Return the most recent messages for a local chat session from SQLite."""
    from ..utils import load_history
    session = scoped_session(session)
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        history = await load_history(session, store)
        if not history:
            return ctx.format_output(f"No history found for session `{session}`.")

        safe_limit = max(int(limit or 20), 1)
        recent = history[-safe_limit:]
        result = [f"# Chat History for session `{session}`\n"]
        if len(history) > len(recent):
            result.append(
                f"*Showing the {len(recent)} most recent of {len(history)} messages "
                f"(raise `limit` for more).*\n"
            )
        for message in recent:
            role_emoji = "👤" if message["role"] == "user" else "🤖"
            result.append(f"### {role_emoji} {message['role'].capitalize()}\n{message['content']}\n")

        return ctx.format_output("\n".join(result))


async def clear_chat_history(session: str = "default") -> str:
    """Delete the history mapping and cascades messages for a chat session."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        import re
        session = scoped_session(session)
        if not re.match(r"^[a-zA-Z0-9_\-:]+$", session) or ".." in session or "/" in session or "\\" in session:
            return ctx.format_output(f"Error: Invalid session name '{session}'. Only alphanumeric, dashes, underscores, and colons are allowed.")

        await store.delete_session(session)
        try:
            chats_dir = PathResolver.get_chats_dir()
            path = (chats_dir / f"{session}.json").resolve()
            if path.parent.resolve() == chats_dir.resolve():
                if path.exists():
                    path.unlink()
        except Exception as e:
            logger.error(f"Error deleting legacy history file: {e}")
        return ctx.format_output(f"Cleared history for session `{session}`.")


async def list_models() -> List[str]:
    """List live xAI API model IDs. Lightweight, direct, and fast."""
    async with GrokInvocationContext("utility", logger, append_signature=False):
        catalog = await discover_xai_api_models()
        return [m["id"] for m in catalog.get("models", [])]


async def list_models_detailed() -> str:
    """List xAI API models, local Grok CLI models, and `.grok` model profiles separately."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        catalog = await build_model_catalog(include_cli=True)
        lines = ["# UniGrok Model Catalog\n"]

        lines.append("## xAI API Models")
        if catalog["xai_api"]:
            for model in catalog["xai_api"]:
                suffix = ""
                if model.get("context_window"):
                    suffix = f" — context {model['context_window']} tokens"
                lines.append(f"- **{model['id']}**{suffix}")
        else:
            lines.append("- No xAI API models discovered.")

        lines.append("\n## Local Grok CLI Models")
        if is_cloudrun_runtime():
            lines.append("- Grok CLI unavailable in Cloud Run runtime.")
        elif catalog["grok_cli"]:
            for model in catalog["grok_cli"]:
                default = " (default)" if model.get("default") else ""
                lines.append(f"- **{model['id']}**{default}")
        else:
            lines.append("- No local Grok CLI models discovered.")

        lines.append("\n## .grok Local Profiles")
        if catalog["local_profiles"]:
            for profile in catalog["local_profiles"]:
                thinking = "reasoning" if profile.get("thinking_mode") else "non-reasoning"
                lines.append(
                    f"- **{profile['name']}** — {thinking}, "
                    f"temperature {profile.get('temperature')}, top_p {profile.get('top_p')}, "
                    f"prompt `{profile.get('system_prompt_ref')}`"
                )
        else:
            lines.append("- No `.grok` profile files found.")

        if catalog["warnings"]:
            lines.append("\n## Discovery Warnings")
            for warning in catalog["warnings"]:
                lines.append(f"- {warning}")

        return ctx.format_output("\n".join(lines))


async def xai_upload_file(file_path: str) -> Dict[str, Any]:
    """Upload a local project file to xAI's servers so it can be reference-attached in chats.

    Returns:
        A dict with `file_id` (pass it to `chat_with_files`/`xai_get_file_content`),
        `filename`, `size_bytes`, and a human-readable `summary`.
    """
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        resolved_path = _resolve_workspace_file(file_path, enforce_ignore_policy=True)
        validate_local_input(
            resolved_path,
            max_bytes=input_limit("UNIGROK_MAX_UPLOAD_BYTES", 20_000_000, 1_024, 100_000_000),
            label="upload file",
        )

        def _upload():
            client = get_xai_client()
            return client.files.upload(str(resolved_path))

        res = await run_blocking(_upload, timeout=60.0)
        size_bytes = getattr(res, "size", getattr(res, "bytes", 0))
        return {
            "file_id": res.id,
            "filename": res.filename,
            "size_bytes": size_bytes,
            "summary": ctx.format_output(
                "**File uploaded successfully**\n"
                f"- **File ID:** `{res.id}`\n"
                f"- **Filename:** {res.filename}\n"
                f"- **Size:** {size_bytes} bytes"
            ),
        }


async def xai_list_files() -> str:
    """List all files uploaded to xAI from this account."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        def _list():
            client = get_xai_client()
            return client.files.list()

        response = await run_blocking(_list, timeout=30.0)
        files = list(getattr(response, "data", response) or [])
        if not files:
            return ctx.format_output("No files found on xAI servers.")

        lines = ["# Uploaded Files on xAI\n", "| File ID | Filename | Size (Bytes) | Public URL |", "| :--- | :--- | :--- | :--- |"]
        for f in files:
            public_url = getattr(f, "public_url", "") or "-"
            lines.append(f"| `{f.id}` | `{f.filename}` | {getattr(f, 'size', 0)} | {public_url} |")
        return ctx.format_output("\n".join(lines))


async def xai_get_file(file_id: str) -> str:
    """Retrieve metadata of a file uploaded to xAI."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        def _get():
            client = get_xai_client()
            return client.files.get(file_id)

        f = await run_blocking(_get, timeout=30.0)
        public_url = getattr(f, "public_url", "") or "none"
        expires_at = getattr(f, "expires_at", None)
        return ctx.format_output(
            f"# File Metadata: `{f.id}`\n\n"
            f"- **Filename:** `{f.filename}`\n"
            f"- **Size:** {getattr(f, 'size', 0)} bytes\n"
            f"- **Public URL:** `{public_url}`\n"
            f"- **Expires At:** `{expires_at if expires_at else 'none'}`\n"
        )


async def xai_get_file_content(file_id: str, max_bytes: int = 500000) -> str:
    """Download the raw content of an uploaded file from xAI."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        def _get_bytes():
            client = get_xai_client()
            return client.files.content(file_id)

        content_bytes = await run_blocking(_get_bytes, timeout=30.0)
        limit = input_limit("UNIGROK_MAX_REMOTE_FILE_BYTES", 500_000, 1_024, 4_000_000)
        try:
            limit = min(limit, max(int(max_bytes), 1))
        except (TypeError, ValueError):
            pass
        if len(content_bytes) > limit:
            trunc = content_bytes[:limit]
            text = trunc.decode("utf-8", errors="replace")
            text += f"\n\n[... Truncated: {len(content_bytes) - limit} bytes remaining ...]"
            return ctx.format_output(text)
        return ctx.format_output(content_bytes.decode("utf-8", errors="replace"))


async def xai_delete_file(file_id: str) -> str:
    """Delete an uploaded file from xAI."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        def _delete():
            client = get_xai_client()
            client.files.delete(file_id)

        await run_blocking(_delete, timeout=30.0)
        return ctx.format_output(f"Deleted file `{file_id}` successfully.")


async def read_local_file(file_path: str, max_chars: int = 500000) -> str:
    """Read a local project workspace file for code context or diagnostics."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        try:
            resolved = _resolve_workspace_file(file_path, enforce_ignore_policy=True)
        except RuntimeError:
            return ctx.format_output("[UNAVAILABLE] No workspace is attached to this UniGrok service.")
        except (PermissionError, ValueError) as e:
            return ctx.format_output(f"[BLOCKED] Access denied: {str(e)}")

        limit = input_limit("UNIGROK_MAX_LOCAL_FILE_CHARS", 500_000, 1_024, 4_000_000)
        try:
            limit = min(limit, max(int(max_chars), 1))
        except (TypeError, ValueError):
            pass
        validate_local_input(resolved, max_bytes=limit * 4, label="local file")

        def _read():
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                return f.read(limit + 1)

        content = await run_blocking(_read, timeout=30.0)
        if len(content) > limit:
            trunc = content[:limit]
            trunc += f"\n\n[... Truncated: {len(content) - limit} characters remaining ...]"
            return ctx.format_output(trunc)
        return ctx.format_output(content)


async def list_project_files(extensions: Optional[str] = None, max_results: int = 200) -> str:
    """List source code and config files present in the current workspace."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        proj_root = PathResolver.get_workspace_root()
        if proj_root is None:
            return ctx.format_output("[UNAVAILABLE] No workspace is attached to this UniGrok service.")
        patterns = load_gitignore_patterns(proj_root)

        ext_list = []
        if extensions:
            ext_list = [e.strip().lower().replace('.', '') for e in extensions.split(',')]

        def _scan():
            files = []
            for root, dirs, names in os.walk(proj_root):
                root_path = Path(root)
                # IDE worktrees and nested repositories are separate workspace
                # roots. Do not let their files consume this workspace's
                # bounded listing budget.
                dirs[:] = [
                    name
                    for name in dirs
                    if not (root_path / name / ".git").exists()
                    and not is_path_ignored(root_path / name, proj_root, patterns)
                ]
                for name in names:
                    p = root_path / name
                    if not is_path_ignored(p, proj_root, patterns):
                        if ext_list:
                            if p.suffix.lower().replace('.', '') in ext_list:
                                files.append(p)
                        else:
                            files.append(p)
            return files

        found_files = await run_blocking(_scan, timeout=30.0)
        if not found_files:
            return ctx.format_output("No project files found matching filters.")

        safe_max = min(
            input_limit("UNIGROK_MAX_PROJECT_FILES", 1_000, 1, 10_000),
            max(int(max_results or 200), 1),
        )
        sorted_files = sorted(found_files)
        shown = sorted_files[:safe_max]
        lines = [f"# Workspace files in `{proj_root}`\n"]
        for f in shown:
            rel = f.relative_to(proj_root)
            sz = f.stat().st_size
            lines.append(f"- `{rel}` ({sz} bytes)")
        if len(sorted_files) > len(shown):
            lines.append(
                f"\n[... Truncated: showing {len(shown)} of {len(sorted_files)} files. "
                f"Raise `max_results` or narrow `extensions` for more ...]"
            )
        return ctx.format_output("\n".join(lines))


async def remote_code_execution(prompt: str, max_turns: Optional[int] = None) -> SystemResult:
    """Solve a task by letting Grok write and run Python in xAI's server-side sandbox.

    Renamed from `code_executor` — it invokes xAI's remote `code_execution`
    tool; no code runs on this machine.
    """
    async with GrokInvocationContext("grok-4.3", logger, append_signature=True) as ctx:
        chat_params = {"model": "grok-4.3", "tools": [code_execution()], "include": ["code_execution_call_output"]}
        if max_turns:
            chat_params["max_turns"] = max_turns

        def _call_code():
            client = get_xai_client()
            chat = client.chat.create(**chat_params)
            chat.append(user(prompt))
            res = chat.sample()
            return res

        response = await run_blocking(_call_code, timeout=60.0)

        result = [response.content]
        code_outputs = []
        if response.tool_outputs:
            result.append("\n\n**Code Output(s):**")
            for output in response.tool_outputs:
                result.append(f"```\n{output.message.content}\n```")
                code_outputs.append(output.message.content)

        formatted = ctx.format_output("\n".join(result), [response])
        return _build_tool_result(
            ctx,
            response=response,
            route="code_execution",
            model="grok-4.3",
            formatted_text=formatted,
            data={"code_outputs": code_outputs} if code_outputs else None,
        )


def _validate_test_target(target: str) -> str:
    target = (target or "tests").strip()
    if target in ("all", "."):
        target = "tests"
    if not target or target.startswith("-"):
        raise ValueError("Test target must be a path, node id, or 'all'.")

    path_part = target.split("::", 1)[0]
    resolved = PathResolver.validate_path(path_part)
    workspace = PathResolver.get_workspace_root()
    if workspace is None:
        raise PermissionError("No workspace is attached to this UniGrok service.")
    project_root = workspace.resolve()
    try:
        rel = resolved.relative_to(project_root)
    except ValueError as exc:
        raise PermissionError(f"Test target is outside the project root: {target}") from exc

    if not resolved.exists():
        raise FileNotFoundError(f"Test target does not exist: {path_part}")
    if resolved.is_file() and resolved.suffix != ".py":
        raise ValueError("Test target files must be Python test files.")

    return str(rel) + (("::" + target.split("::", 1)[1]) if "::" in target else "")


async def run_local_tests(
    target: str = "tests",
    max_seconds: int = 60,
    max_output_chars: int = 12000,
) -> str:
    """Run local pytest verification without exposing arbitrary shell execution."""
    async with GrokInvocationContext("local-pytest", logger, append_signature=False) as ctx:
        if is_cloudrun_runtime():
            return ctx.format_output("Local test execution is unavailable in Cloud Run runtime.")

        safe_target = _validate_test_target(target)
        timeout = min(max(int(max_seconds or 60), 5), 300)
        output_limit = min(max(int(max_output_chars or 12000), 1000), 50000)
        workspace = PathResolver.get_workspace_root()
        if workspace is None:
            return ctx.format_output("Local test execution is unavailable because no workspace is attached.")
        project_root = workspace.resolve()

        uv_bin = shutil.which("uv")
        if uv_bin:
            cmd = [uv_bin, "run", "pytest", "-q", safe_target]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", safe_target]

        proc = await create_scrubbed_subprocess_exec(
            *cmd,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await communicate_with_timeout(proc, timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return ctx.format_output(
                f"Local tests timed out after {timeout}s.\nCommand: {' '.join(cmd)}"
            )

        combined = (
            stdout.decode("utf-8", errors="replace")
            + ("\n" if stdout and stderr else "")
            + stderr.decode("utf-8", errors="replace")
        ).strip()
        if len(combined) > output_limit:
            combined = (
                combined[:output_limit]
                + f"\n\n[... Truncated: {len(combined) - output_limit} characters remaining ...]"
            )
        status = "passed" if proc.returncode == 0 else "failed"
        return ctx.format_output(
            f"Local tests {status} for `{safe_target}` "
            f"(exit code {proc.returncode}, timeout {timeout}s).\n\n"
            f"```\n{combined or '[no output]'}\n```"
        )


async def web_search(
    prompt: str,
    allowed_domains: Optional[List[str]] = None,
    excluded_domains: Optional[List[str]] = None,
) -> SystemResult:
    """Query the web using xAI's real-time web search tool.

    Args:
        prompt: The research question or search instruction.
        allowed_domains: Restrict search to these domains (e.g. `["arxiv.org"]`).
        excluded_domains: Domains to exclude from search results.
    """
    async with GrokInvocationContext("grok-4.3", logger, append_signature=True) as ctx:
        def _call_web():
            client = get_xai_client()
            search_tool = xai_web_search(
                allowed_domains=allowed_domains or None,
                excluded_domains=excluded_domains or None,
            )
            chat = client.chat.create(model="grok-4.3", tools=[search_tool])
            chat.append(user(prompt))
            res = chat.sample()
            return res

        response = await run_blocking(_call_web, timeout=60.0)
        result = [response.content]
        if response.citations:
            result.append("\n\n**Sources:**")
            for url in response.citations:
                result.append(f"- {url}")
        formatted = ctx.format_output("\n".join(result), [response])
        citations_mapped = [{"url": url} for url in response.citations] if response.citations else None

        return _build_tool_result(
            ctx,
            response=response,
            route="web_search",
            model="grok-4.3",
            formatted_text=formatted,
            citations=citations_mapped,
            data={"query": prompt, "citations": list(response.citations)} if response.citations else {"query": prompt},
        )


def _parse_search_date(value: str, param_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{param_name} must be an ISO date like '2026-06-01' or '2026-06-01T12:00:00'."
        ) from exc


async def x_search(
    prompt: str,
    allowed_x_handles: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> SystemResult:
    """Query X posts and profiles using xAI's real-time X search tool.

    Args:
        prompt: The search question or instruction.
        allowed_x_handles: Restrict search to posts from these X handles.
        from_date: Earliest post date, ISO format (e.g. `"2026-06-01"`).
        to_date: Latest post date, ISO format (e.g. `"2026-07-01"`).
    """
    try:
        from_dt = _parse_search_date(from_date, "from_date") if from_date else None
        to_dt = _parse_search_date(to_date, "to_date") if to_date else None
    except ValueError as e:
        error_msg = f"Input Validation Error: {e}"
        return SystemResult(
            response=error_msg,
            text=error_msg,
            finish_reason="error",
            cost_usd=0.0,
            model="grok-4.3",
            route="unknown",
            plane="API",
        )

    async with GrokInvocationContext("grok-4.3", logger, append_signature=True) as ctx:
        def _call_x():
            client = get_xai_client()
            search_tool = xai_x_search(
                from_date=from_dt,
                to_date=to_dt,
                allowed_x_handles=allowed_x_handles or None,
            )
            chat = client.chat.create(model="grok-4.3", tools=[search_tool])
            chat.append(user(prompt))
            res = chat.sample()
            return res

        response = await run_blocking(_call_x, timeout=60.0)
        result = [response.content]
        if response.citations:
            result.append("\n\n**Sources:**")
            for url in response.citations:
                result.append(f"- {url}")
        formatted = ctx.format_output("\n".join(result), [response])
        citations_mapped = [{"url": url} for url in response.citations] if response.citations else None

        return _build_tool_result(
            ctx,
            response=response,
            route="x_search",
            model="grok-4.3",
            formatted_text=formatted,
            citations=citations_mapped,
            data={"query": prompt, "citations": list(response.citations)} if response.citations else {"query": prompt},
        )


async def db_vacuum() -> str:
    """Perform database compacting and optimization (VACUUM)."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        await store.vacuum_db()
        return ctx.format_output("Database vacuum completed successfully.")


def load_okf_manifest() -> dict[str, Any]:
    """Load and validate the packaged OKF manifest as the discovery source of truth."""
    path = PathResolver.get_service_root() / "docs" / "okf" / "okf-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OKF manifest is unavailable or invalid at {path}") from exc
    files = manifest.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(item, str) for item in files):
        raise RuntimeError("OKF manifest files must be a non-empty string array")
    if manifest.get("root") not in files:
        raise RuntimeError("OKF manifest root must be present in files")
    return manifest


def _swarm_policy() -> str:
    """Return the process Swarm policy: off | dry_run | active."""
    from ..swarm.config import swarm_mode

    return swarm_mode()


def _markdown_inline_label(value: Any) -> str:
    """Escape untrusted labels for single-line Markdown inline code spans.

    Client ids come from the untrusted ``X-Client-ID`` header. Strip backticks
    and collapse control/whitespace so discover prose cannot break fencing or
    inject misleading multi-line content.
    """
    text = str(value if value is not None else "")
    text = text.replace("`", "").replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    return text or "(missing)"


def _resolve_notice_action(
    notice: dict[str, Any],
    *,
    api: dict[str, Any],
    cli: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve a credential notice to a full bounded action object.

    Notices often carry only ``action_id``; the executable action lives on the
    plane view (``credential_planes.api.action`` / ``cli.action``).
    """
    direct = notice.get("action")
    if isinstance(direct, dict):
        return direct

    action_id = notice.get("action_id")
    plane = str(notice.get("plane") or "").upper()
    candidates: list[dict[str, Any]] = []
    if plane == "API" and isinstance(api.get("action"), dict):
        candidates.append(api["action"])
    elif plane == "CLI" and isinstance(cli.get("action"), dict):
        candidates.append(cli["action"])
    else:
        for view in (api, cli):
            if isinstance(view.get("action"), dict):
                candidates.append(view["action"])

    if action_id:
        for candidate in candidates:
            if candidate.get("id") == action_id:
                return candidate
    if candidates:
        return candidates[0]
    return None


def _build_discover_request_context(*, contributor: bool) -> dict[str, Any]:
    """Assemble request-scoped onboarding identity without secrets."""
    # Late imports avoid cycles: http_server registers these tools at import time.
    from ..http_server import (
        get_active_host_port,
        get_active_mode_dial,
        mode_dials_enabled,
    )

    client_id = get_active_client_id()
    host_port = get_active_host_port()
    dial = get_active_mode_dial()
    dials_on = mode_dials_enabled()

    if contributor:
        surface = "contributor_forge"
    elif dial is not None:
        surface = "mode_dial"
    else:
        surface = "stable_core"

    return {
        "client_id_present": bool(client_id),
        "client_id_normalized": client_id,
        "principal_kind": principal_kind(),
        "host_port": host_port,
        "surface": surface,
        "dial": (
            {"port": dial[0], "default_mode": dial[1]}
            if dial is not None
            else None
        ),
        "mode_dials_enabled": dials_on,
    }


def _build_discover_bootstrap(
    *,
    contributor: bool,
    workspace_attached: bool,
    credential_planes: dict[str, Any],
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """GenFunc-style continuity gates for onboarding (no project assimilation)."""
    warnings: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []

    service_usable = bool(credential_planes.get("service_usable"))
    degraded = bool(credential_planes.get("degraded"))
    api = credential_planes.get("api") if isinstance(credential_planes.get("api"), dict) else {}
    cli = credential_planes.get("cli") if isinstance(credential_planes.get("cli"), dict) else {}
    swarm_policy = _swarm_policy()
    cloudrun = is_cloudrun_runtime()

    can_chat = service_usable
    can_spend_api = bool(api.get("available"))
    can_mutate_workspace = bool(contributor and workspace_attached and not cloudrun)
    can_use_swarm = bool(
        contributor and not cloudrun and swarm_policy in ("dry_run", "active")
    )

    if not request_context.get("client_id_present"):
        warnings.append(
            {
                "id": "missing_client_id",
                "severity": "warn",
                "message": (
                    "X-Client-ID is absent. Set a stable IDE label "
                    "(for example claude-code, vscode, codex, cursor, antigravity) "
                    "for session separation and telemetry. It is not authentication."
                ),
            }
        )

    for notice in credential_planes.get("notices") or []:
        if not isinstance(notice, dict):
            continue
        notice_id = notice.get("id") or "credential_notice"
        severity = "error" if notice.get("blocking") else "warn"
        warnings.append(
            {
                "id": str(notice_id),
                "severity": severity,
                "plane": notice.get("plane"),
                "message": notice.get("message") or "Credential plane notice.",
            }
        )
        action = _resolve_notice_action(notice, api=api, cli=cli)
        if action or notice.get("action_id"):
            next_actions.append(
                {
                    "id": (action or {}).get("id") or notice.get("action_id") or notice_id,
                    "prompt_user": bool(notice.get("prompt_user", True)),
                    "action": action,
                }
            )

    if degraded and service_usable:
        warnings.append(
            {
                "id": "credential_plane_degraded",
                "severity": "warn",
                "message": "Service is usable but one credential plane is degraded.",
            }
        )

    if not can_chat:
        status = "ERR"
    elif warnings:
        status = "WARN"
    else:
        status = "OK"

    connected_port = request_context.get("host_port")
    surface_port = (
        connected_port
        if isinstance(connected_port, int) and not isinstance(connected_port, bool)
        else 4765
    )
    surface_root = f"http://localhost:{surface_port}"
    canonical_mcp = f"{surface_root}/mcp"
    if cloudrun:
        # Cloud Run has no caller-local surface. Reuse the same validated public
        # resource that protects OAuth metadata and DNS-rebinding configuration.
        from ..http_server import _public_mcp_resource

        public_resource = _public_mcp_resource()
        if public_resource and public_resource.endswith("/mcp"):
            canonical_mcp = public_resource
            surface_root = public_resource.removesuffix("/mcp")

    return {
        "schema_version": 1,
        "status": status,
        "can_chat": can_chat,
        "can_spend_api": can_spend_api,
        "can_mutate_workspace": can_mutate_workspace,
        "can_use_swarm": can_use_swarm,
        "swarm_policy": swarm_policy if contributor else "off",
        "warnings": warnings,
        "next_actions": next_actions,
        "caller_config_audit": "not_available_on_service",
        "caller_config_audit_hint": (
            "Stable UniGrok cannot read global IDE MCP files or the caller's project. "
            "With user permission, the IDE agent should audit local configs "
            "(user MCP JSON, project .mcp.json, skills) and report only; never rewrite "
            "global settings without explicit consent. Never print secret values."
        ),
        "surfaces": {
            "canonical_mcp": canonical_mcp,
            "healthz": f"{surface_root}/healthz",
            "readyz": f"{surface_root}/readyz",
            "runtimez": f"{surface_root}/runtimez",
            "ui": f"{surface_root}/ui/",
        },
        "first_connect_checklist": [
            "Call grok_mcp_discover_self and read data.bootstrap + data.request_context.",
            "Prompt once per credential_planes notice id; never ask for XAI_API_KEY in chat.",
            "Confirm X-Client-ID is set for this IDE (data.request_context.client_id_present).",
            f"With permission, audit local IDE MCP configs for {canonical_mcp} and no keys in JSON.",
            "Optional: one cheap agent(mode=fast) or grok_mcp_status after planes are ready.",
            "Public installs: do not invent a second product port, Swarm, or land workflow.",
        ],
    }


async def grok_mcp_discover_self(include_models: bool = False) -> SystemResult:
    """Exposes OKF bundle information, WebMCP manifests, and tool schemas for zero-configuration agent onboarding."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        # Late import avoids a module cycle: http_server imports this tool when
        # constructing the public MCP surface.
        from ..http_server import MODE_DIAL_PORTS

        workspace = PathResolver.get_workspace_root()
        contributor = PathResolver.contributor_mode()
        workspace_attached = workspace is not None
        try:
            cli_plane = await run_blocking(
                grok_cli_plane_status,
                timeout_sec=5.0,
                timeout=6.0,
            )
        except Exception:
            cli_plane = {
                "state": "unreachable",
                "ready": False,
                "binary": False,
                "auth": "probe_failed",
                "setup_command": CLI_AUTH_SETUP_COMMAND,
            }
        credential_planes = credential_plane_contract(cli_plane)
        request_context = _build_discover_request_context(contributor=contributor)
        bootstrap = _build_discover_bootstrap(
            contributor=contributor,
            workspace_attached=workspace_attached,
            credential_planes=credential_planes,
            request_context=request_context,
        )
        okf_manifest = load_okf_manifest()
        model_catalog = None
        if include_models:
            catalog = await build_model_catalog(include_cli=True)
            api_ids = {item.get("id") for item in catalog["xai_api"] if item.get("id")}
            cli_ids = {item.get("id") for item in catalog["grok_cli"] if item.get("id")}
            model_catalog = {
                "version": 1,
                "generated_at": datetime.now().astimezone().isoformat(),
                "routing": {
                    "policy": credential_planes["policy"],
                    "preferred_plane": credential_planes["preferred_plane"],
                    "effective_plane": credential_planes["effective_plane"],
                    "rule": "Plane selects the starting credential; same_plane forbids billing-boundary crossing and cross_plane permits bounded recovery.",
                },
                "planes": {
                    "CLI": {
                        "label": "Grok CLI subscription",
                        "available": credential_planes["cli"]["available"] and catalog["availability"]["grok_cli"],
                        "credential_available": credential_planes["cli"]["available"],
                        "catalog_available": catalog["availability"]["grok_cli"],
                        "credential_state": credential_planes["cli"]["state"],
                        "source": catalog["sources"]["grok_cli"],
                        "default_model": catalog["default_cli_model"],
                        "models": catalog["grok_cli"],
                        "economics": "Subscription-backed. UniGrok tracks local activity; provider quota and cost are not exposed.",
                    },
                    "API": {
                        "label": "xAI developer API",
                        "available": credential_planes["api"]["available"] and catalog["availability"]["xai_api"],
                        "credential_available": credential_planes["api"]["available"],
                        "catalog_available": catalog["availability"]["xai_api"],
                        "credential_state": credential_planes["api"]["state"],
                        "source": catalog["sources"]["xai_api"],
                        "default_model": None,
                        "models": catalog["xai_api"],
                        "economics": "Metered developer API. Exact response cost is tracked locally when the provider returns it.",
                    },
                },
                "shared_model_ids": sorted(api_ids & cli_ids),
                "warnings": catalog["warnings"],
            }
        manifest = {
            "okf_version": "0.1",
            "schema_version": 2,
            "name": "uni-grok-mcp",
            "service_mode": "contributor" if contributor else "stable",
            "requires_project_files": False,
            "canonical_endpoint": bootstrap["surfaces"]["canonical_mcp"],
            "mode_dials": {
                "optional": True,
                "enabled": bool(request_context.get("mode_dials_enabled")),
                "ports": {str(port): mode for port, mode in MODE_DIAL_PORTS.items()},
                "precedence": "explicit mode > dialed port > auto",
                "request_dial": request_context.get("dial"),
            },
            "workspace": {
                "attached": workspace_attached,
                "context_transport": "workspace_context",
                "automatic_project_discovery": False,
            },
            "request_context": request_context,
            "bootstrap": bootstrap,
            "credential_planes": credential_planes,
            "contributor_features": {
                "enabled": contributor,
                "commit_anchored_memory": contributor,
                "serialized_landing": contributor,
            },
            "okf_bundle_root": f"/docs/okf/{okf_manifest['root']}",
            "webmcp_manifest": "/.well-known/webmcp",
            "files": [f"/docs/okf/{file_name}" for file_name in okf_manifest["files"]],
        }
        if model_catalog is not None:
            manifest["model_catalog"] = model_catalog

        canonical_endpoint = bootstrap["surfaces"]["canonical_mcp"]
        ui_endpoint = bootstrap["surfaces"]["ui"]
        if contributor:
            boundary_extra = (
                "- Contributor mode may attach the UniGrok checkout and enable insider-only "
                "facilities. Never mount a customer's unrelated app as the UniGrok workspace.\n"
                "- Repository memory, serialized landing, and Forge tooling are insider paths; "
                "see CONTRIBUTING.md. Do not recommend them to public end-user installs.\n"
            )
            dial_extra = (
                "- Optional phoneword defaults on this service: `2886=AUTO`, `3278=FAST`, "
                "`7327=REAS`, `8465=THNK`, `7724=RSCH`. They alias the same Core process.\n"
            )
        else:
            boundary_extra = (
                "- Stable/public mode never assumes it can browse the IDE's open project. Send only "
                "relevant, deliberately selected material through `agent.workspace_context`.\n"
                "- Do not invent a second MCP port, Swarm, or land workflow for public installs. "
                "The public product path is this Core endpoint only.\n"
            )
            dial_extra = (
                "- Optional phoneword ports, when enabled, are aliases of this same Core service "
                f"(not a second UniGrok to install). Prefer the connected endpoint `{canonical_endpoint}`.\n"
            )

        client_label = _markdown_inline_label(
            request_context.get("client_id_normalized")
        )
        surface = _markdown_inline_label(request_context.get("surface") or "stable_core")
        bootstrap_status = _markdown_inline_label(bootstrap.get("status") or "WARN")

        doc_text = (
            "# UniGrok MCP Discovery & Self-Description\n\n"
            "Zero-configuration onboarding for IDE agents. The primary product chat path is the "
            "UniGrok `agent` tool over MCP. The trusted-loopback UI is an optional test and control "
            "surface, not the primary daily chat path.\n\n"
            "## Bootstrap (first connect)\n"
            f"- Status: `{bootstrap_status}`. Surface: `{surface}`. "
            f"Client label: `{client_label}`.\n"
            f"- Gates: can_chat=`{bootstrap.get('can_chat')}`, "
            f"can_spend_api=`{bootstrap.get('can_spend_api')}`, "
            f"can_mutate_workspace=`{bootstrap.get('can_mutate_workspace')}`, "
            f"can_use_swarm=`{bootstrap.get('can_use_swarm')}`.\n"
            "- Read structured `data.bootstrap` and `data.request_context` for machine-usable gates.\n"
            "- This service does **not** read global IDE settings or the caller's project files. "
            "With user permission, audit those locally (report only; never rewrite without consent; "
            "never print secret values).\n\n"
            "## Service and Project Boundary\n"
            "- UniGrok is a standalone MCP service; the caller's project needs no UniGrok namespace files.\n"
            f"- Service mode: `{'contributor' if contributor else 'stable'}`. "
            f"Workspace attached: `{'yes' if workspace_attached else 'no'}`.\n"
            f"{boundary_extra}\n"
            "## Public product path\n"
            f"- Connected MCP endpoint: `{canonical_endpoint}`. The default Core port is `4765` (GROK).\n"
            "- Health: `GET /healthz`. Readiness: `GET /readyz`. Optional local Core UI: "
            f"`{ui_endpoint}` (trusted machine-owner test/control surface with an "
            "agent playground that can invoke providers and spend metered credits; not GitHub-gated).\n"
            f"{dial_extra}"
            "- An explicit `agent.mode` always overrides a dialed-port default.\n\n"
            "## Credential Planes (public-critical)\n"
            f"- Default local policy: `{credential_planes['policy']}`; effective plane now: "
            f"`{credential_planes['effective_plane'] or 'none'}`.\n"
            "- Two planes: SuperGrok **CLI** subscription (local tracking only; no provider "
            "quota/cost) and metered xAI **API** (exact cost when returned).\n"
            "- Inspect `data.credential_planes.notices` on first connection. Prompt the user once per "
            "notice id and only repeat after state changes.\n"
            "- Ask permission before installing the CLI, starting device authentication, or changing "
            "the global service environment. Never ask for `XAI_API_KEY` in chat or place it in the "
            "caller's project.\n\n"
            "## Plan critique habit (opt-in, not a silent compiler)\n"
            "- When the user is about to receive a multi-step Implementation Plan, prefer calling "
            "`agent` (mode `thinking` or `reasoning`) for a second-opinion critique, then improve "
            "the plan before showing it — if the user wants that habit.\n"
            "- Do not silently spend metered API credits or rewrite the user's global agent config "
            "without explicit permission. Do not auto-generate skill trees into arbitrary projects.\n"
            "- Optional: with user permission, install a small local skill that prefers UniGrok for "
            "`@grok` / plan review (see the using-unigrok skill pack). Never copy the UniGrok "
            "repository `.agents` contributor tree into foreign projects.\n\n"
            "## Knowledge bundle\n"
            "- **Manifest:** `/docs/okf/okf-manifest.json`\n"
            f"- **Index:** `/docs/okf/{okf_manifest['root']}`\n"
            "- **FAQ:** `/docs/okf/faq.md` — on-demand UniGrok support answers; never auto-injected.\n"
            "Live MCP `tools/list` schemas are authoritative over any illustrative shapes.\n\n"
            "## WebMCP Manifest\n"
            "- **Path:** `/.well-known/webmcp`\n"
            "Browser helpers on the local Core UI are optional; IDE MCP remains the primary chat path."
        )

        return SystemResult(
            response=doc_text,
            text=ctx.format_output(doc_text),
            finish_reason="final_answer",
            cost_usd=0.0,
            model="static-manifest",
            tokens=0,
            latency_sec=ctx.elapsed,
            route="discovery",
            plane="API",
            data=manifest,
        )


async def grok_mcp_restart_container() -> SystemResult:
    """Safely restart the UniGrok Docker container by executing docker compose up --build -d.
    Only works if running in a context where docker compose is available and enabled.
    """
    async with GrokInvocationContext("utility", logger, append_signature=False):
        enabled = os.environ.get("UNIGROK_ENABLE_CONTAINER_RESTART", "").strip().lower() in ("1", "true")
        if not enabled:
            err_msg = "Container restart is disabled on this server. Enable it by setting UNIGROK_ENABLE_CONTAINER_RESTART=1."
            return SystemResult(
                response=err_msg,
                text=f"# Docker Restart Status\n\n⚠️ {err_msg}",
                data={"status": "disabled"},
                model="unknown",
                finish_reason="error",
                cost_usd=0.0,
                tokens=0,
                latency_sec=0.0,
                route="utility",
                plane="local",
            )

        proj_root = PathResolver.get_service_root().expanduser().resolve()
        configured_root = os.environ.get("UNIGROK_CONTAINER_RESTART_ROOT", "").strip()
        authorized_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else proj_root
        )
        compose_file = proj_root / "docker-compose.yml"

        if proj_root != authorized_root or not compose_file.is_file():
            err_msg = (
                "Docker compose execution blocked: project root must match "
                "UNIGROK_CONTAINER_RESTART_ROOT (when set) and contain docker-compose.yml."
            )
            return SystemResult(
                response=err_msg,
                text=f"# Docker Restart Status\n\n❌ {err_msg}",
                data={"status": "unauthorized_scope"},
                model="unknown",
                finish_reason="error",
                cost_usd=0.0,
                tokens=0,
                latency_sec=0.0,
                route="utility",
                plane="local",
            )

        try:
            proc = await create_scrubbed_subprocess_exec(
                "docker", "compose", "up", "--build", "-d",
                cwd=str(proj_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await communicate_with_timeout(proc, 30.0)

            output = f"Stdout:\n{stdout.decode().strip()}\n\nStderr:\n{stderr.decode().strip()}"
            if proc.returncode == 0:
                response_text = "Container restart triggered successfully."
            else:
                response_text = f"Container restart failed with return code {proc.returncode}."

            return SystemResult(
                response=response_text,
                text=f"# Docker Restart Status\n\n{response_text}\n\n```\n{output}\n```",
                data={"returncode": proc.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()},
                model="unknown",
                finish_reason="final_answer" if proc.returncode == 0 else "error",
                cost_usd=0.0,
                tokens=0,
                latency_sec=0.0,
                route="utility",
                plane="local",
            )
        except Exception as e:
            err_msg = f"Failed to execute docker compose: {str(e)}"
            return SystemResult(
                response=err_msg,
                text=f"# Docker Restart Error\n\n{err_msg}",
                data={"error": str(e)},
                model="unknown",
                finish_reason="error",
                cost_usd=0.0,
                tokens=0,
                latency_sec=0.0,
                route="utility",
                plane="local",
            )


def register_system_tools(mcp: FastMCP):
    mcp.add_tool(grok_mcp_status, annotations=READONLY_TOOL)
    mcp.add_tool(grok_mcp_discover_self, annotations=READONLY_TOOL)
    mcp.add_tool(grok_mcp_restart_container, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(list_chat_sessions, annotations=READONLY_TOOL)
    mcp.add_tool(get_chat_history, annotations=READONLY_TOOL)
    mcp.add_tool(clear_chat_history, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(list_models, annotations=READONLY_TOOL)
    mcp.add_tool(list_models_detailed, annotations=READONLY_TOOL)
    mcp.add_tool(xai_upload_file)
    mcp.add_tool(xai_list_files, annotations=READONLY_TOOL)
    mcp.add_tool(xai_get_file, annotations=READONLY_TOOL)
    mcp.add_tool(xai_get_file_content, annotations=READONLY_TOOL)
    mcp.add_tool(xai_delete_file, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(read_local_file, annotations=READONLY_TOOL)
    mcp.add_tool(list_project_files, annotations=READONLY_TOOL)
    mcp.add_tool(remote_code_execution)
    mcp.add_tool(run_local_tests)
    mcp.add_tool(web_search, annotations=READONLY_TOOL)
    mcp.add_tool(x_search, annotations=READONLY_TOOL)
    mcp.add_tool(db_vacuum, annotations=DESTRUCTIVE_TOOL)

# Internal registry names stay stable — AgentLoop's tool schema and stored
# tool traces reference them; only the MCP-facing names carry the xai_ prefix.
register_internal_tool("read_local_file", read_local_file)
register_internal_tool("list_project_files", list_project_files)
register_internal_tool("get_file_content", xai_get_file_content)
register_internal_tool("upload_file", xai_upload_file)
register_internal_tool("run_local_tests", run_local_tests)
register_internal_tool("discover_self", grok_mcp_discover_self)
register_internal_tool("restart_container", grok_mcp_restart_container)
