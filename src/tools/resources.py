# src/tools/resources.py
# MCP resources (grok:// URIs) and reusable prompts mirroring the read-only
# tool surface. The tools they mirror stay registered — client support for
# resources/prompts varies, so both surfaces coexist by design.
#
# Introspected against the installed FastMCP: @mcp.resource supports static
# URIs and {param} templates (function params must match the URI params) and
# @mcp.prompt registers prompt callables with typed arguments.

import json
import logging
import time
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from ..jobs import get_job_manager
from ..utils import (
    PathResolver,
    build_model_catalog,
    get_circuit_breaker_state,
    get_routing_advisor,
    get_runtime_stats,
    load_history,
    store,
)

logger = logging.getLogger("GrokMCP")


def _to_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


# ── grok://workspace bounds ──────────────────────────────────────────────────
# The workspace document is read by every connected agent, so every section
# is clamped: agent-instruction files per-file, the git log block, the
# session listing, and the assembled document as a whole.
# Keep enough headroom for the shared rules' coordination sections while the
# total resource clamp still bounds the payload sent to every connected agent.
_WORKSPACE_DOC_LIMIT = 8000
_WORKSPACE_TOTAL_LIMIT = 24000
_WORKSPACE_SESSION_LIMIT = 20
_WORKSPACE_GIT_TTL_SEC = 30.0
_workspace_git_cache: Dict[str, Any] = {"at": 0.0, "text": ""}


def _clamp(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated at {limit} chars ...]"


def _read_agent_doc(rel_path: str) -> Optional[str]:
    """One agent-instructions file (bounded), or None when absent/unreadable."""
    try:
        workspace = PathResolver.get_workspace_root()
        if workspace is None:
            return None
        path = workspace / rel_path
        if not path.is_file():
            return None
        return _clamp(path.read_text(encoding="utf-8", errors="replace"), _WORKSPACE_DOC_LIMIT)
    except Exception as exc:
        logger.debug(f"workspace resource: could not read {rel_path}: {exc}")
        return None


async def _workspace_git_summary() -> str:
    """Current branch + last 5 commits via the existing git plumbing
    (src/tools/git.py), TTL-cached so concurrent agents polling the resource
    don't fork git per read. Cloud Run (git reads unavailable) and git
    failures degrade to a plain 'unavailable' line."""
    now = time.time()
    if _workspace_git_cache["text"] and (now - _workspace_git_cache["at"]) < _WORKSPACE_GIT_TTL_SEC:
        return _workspace_git_cache["text"]
    # Late import: git.py registers internal tools at import time; resources
    # only needs the two read helpers.
    from .git import git_current_branch, git_log

    try:
        branch = await git_current_branch()
    except Exception as exc:
        branch = f"unavailable ({exc})"
    try:
        log = await git_log(limit=5)
    except Exception as exc:
        log = f"unavailable ({exc})"
    text = f"Branch: `{branch}`\n\nLast 5 commits:\n```\n{_clamp(log, 2000)}\n```"
    _workspace_git_cache["at"] = now
    _workspace_git_cache["text"] = text
    return text


def register_resource_primitives(mcp: FastMCP):
    """Register the grok:// resources and the reusable prompts."""

    @mcp.resource(
        "grok://models",
        description="The UniGrok model catalog: xAI API models, local Grok CLI models, and .grok profiles.",
        mime_type="application/json",
    )
    async def models_resource() -> str:
        return _to_json(await build_model_catalog(include_cli=True))

    @mcp.resource(
        "grok://status",
        description="Server health, storage, and runtime telemetry — the grok_mcp_status payload.",
        mime_type="text/markdown",
    )
    async def status_resource() -> str:
        from .system import grok_mcp_status

        return await grok_mcp_status()

    @mcp.resource(
        "grok://sessions",
        description="All stored chat sessions (name, model, last_active).",
        mime_type="application/json",
    )
    async def sessions_resource() -> str:
        return _to_json(await store.list_sessions())

    @mcp.resource(
        "grok://sessions/{name}",
        description="Full message history for one chat session.",
        mime_type="application/json",
    )
    async def session_history_resource(name: str) -> str:
        return _to_json(await load_history(name, store))

    @mcp.resource(
        "grok://jobs/{job_id}",
        description="Status/result view of one deferred research job.",
        mime_type="application/json",
    )
    async def job_resource(job_id: str) -> str:
        view = await get_job_manager().get(job_id)
        return _to_json(view if view is not None else {"status": "not_found", "job_id": job_id})

    @mcp.resource(
        "grok://knowledge",
        description="The workspace knowledge memory: most recent distilled facts.",
        mime_type="application/json",
    )
    async def knowledge_resource() -> str:
        from .knowledge import _fact_view

        rows = await store.list_facts(limit=50)
        return _to_json({
            "count": await store.count_facts(),
            "facts": [_fact_view(row) for row in rows],
        })

    @mcp.resource(
        "grok://workspace",
        description=(
            "The shared multi-agent workspace picture: agent ground rules "
            "(.agents/AGENTS.md, .gemini/GEMINI.md), current git branch and "
            "recent commits, active sessions, and advisor/breaker/runtime state."
        ),
        mime_type="text/markdown",
    )
    async def workspace_resource() -> str:
        """One bounded document any connected agent (Claude/Codex/Gemini) can
        read to orient itself in this workspace. Every section degrades
        independently — a missing doc, broken git, or unhappy store never
        fails the whole resource."""
        workspace = PathResolver.get_workspace_root()
        if workspace is None:
            return (
                "# UniGrok Workspace\n\n"
                "No local workspace is attached. This is the normal stable-service "
                "mode: the caller's project remains private unless the caller sends "
                "selected context with an `agent` request."
            )
        sections = ["# UniGrok Workspace"]

        agents_doc = _read_agent_doc(".agents/AGENTS.md")
        if agents_doc:
            sections.append("## Agent Ground Rules (.agents/AGENTS.md)\n" + agents_doc)
        gemini_doc = _read_agent_doc(".gemini/GEMINI.md")
        if gemini_doc:
            sections.append("## Gemini Agent Notes (.gemini/GEMINI.md)\n" + gemini_doc)

        sections.append("## Git\n" + await _workspace_git_summary())

        try:
            sessions = await store.list_sessions()
        except Exception as exc:
            logger.debug(f"workspace resource: session listing failed: {exc}")
            sessions = []
        if sessions:
            lines = [
                f"- `{row.get('session_name')}` (last active {row.get('last_active')})"
                for row in sessions[:_WORKSPACE_SESSION_LIMIT]
            ]
            if len(sessions) > _WORKSPACE_SESSION_LIMIT:
                lines.append(f"- [... {len(sessions) - _WORKSPACE_SESSION_LIMIT} more sessions ...]")
            sections.append("## Active Sessions\n" + "\n".join(lines))
        else:
            sections.append("## Active Sessions\nnone")

        state_lines = []
        try:
            runtime = get_runtime_stats()
            state_lines.append(
                f"- Timed threads: {runtime['timed_threads_in_flight']} in flight "
                f"(peak {runtime['timed_threads_peak']})"
            )
        except Exception:
            pass
        try:
            breakers = get_circuit_breaker_state()
            open_models = sorted(m for m, s in breakers.items() if s.get("open"))
            state_lines.append(
                "- Circuit breakers open: "
                + (", ".join(f"`{m}`" for m in open_models) if open_models else "none")
            )
        except Exception:
            pass
        try:
            advisor = await get_routing_advisor().status_view(store)
            state_lines.append(
                f"- Routing advisor borderline choice: {advisor['borderline_choice']} "
                f"(source: {advisor['borderline_source']})"
            )
        except Exception:
            pass
        sections.append("## Runtime State\n" + ("\n".join(state_lines) or "unavailable"))

        return _clamp("\n\n".join(sections), _WORKSPACE_TOTAL_LIMIT)

    @mcp.prompt(description="Deep multi-source research on a topic, with citations.")
    def research_topic(topic: str) -> str:
        return (
            "Research the topic below thoroughly.\n\n"
            f"Topic: {topic}\n\n"
            "Requirements:\n"
            "- Use web_search and x_search for current sources; cross-check every "
            "important claim against at least two independent sources.\n"
            "- Distinguish established facts from speculation and note where "
            "sources disagree.\n"
            "- Cite each nontrivial claim with its source URL.\n"
            "- End with a short list of open questions.\n\n"
            "For investigations too long for one turn, submit_research_job runs "
            "this as a deferred background job."
        )

    @mcp.prompt(description="Fix a bug or failing behavior and prove it with tests.")
    def fix_and_test(path_or_description: str) -> str:
        return (
            "Fix the following and prove the fix.\n\n"
            f"Target: {path_or_description}\n\n"
            "Process:\n"
            "1. Locate and reproduce the problem (read_local_file, "
            "list_project_files, git_diff, git_log).\n"
            "2. Make the smallest change that fixes the root cause — no drive-by "
            "refactoring.\n"
            "3. Run run_local_tests and iterate until the relevant tests pass.\n"
            "4. Report the change, the test evidence, and any remaining risks."
        )
