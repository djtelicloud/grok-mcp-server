# src/tools/research.py
# Deferred research-job tools: submit/get/list over the JobManager.

import logging
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ..jobs import get_job_manager
from ..utils import caller_from_mcp_context

logger = logging.getLogger("GrokMCP")

READONLY_TOOL = ToolAnnotations(readOnlyHint=True)


async def submit_research_job(
    prompt: str,
    model: Optional[str] = None,
    agent_count: Optional[int] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Submit a long-running research task as a deferred xAI job and return
    immediately. The job runs in the background with xAI's server-side web
    search, X search, and code-execution tools attached; poll
    `get_research_job(job_id)` for the result.

    Args:
        prompt: The research question or task.
        model: Optional Grok model id. Leave unset to use the planning model.
        agent_count: Optional multi-agent fan-out — only 4 or 16 are accepted.

    Returns:
        A dict with `job_id` (pass it to `get_research_job`), `status`
        (`"queued"`), and the resolved `model`.
    """
    text = str(prompt or "").strip()
    if not text:
        return {"error": "Input Validation Error: prompt must not be empty."}
    if agent_count is not None and agent_count not in (4, 16):
        return {"error": "Input Validation Error: agent_count must be either 4 or 16."}
    # ctx is FastMCP-injected (hidden from the tool schema): the clientInfo
    # name identifies which agent submitted the job on the persisted row.
    return await get_job_manager().submit(
        text,
        model=model,
        agent_count=agent_count,
        caller=caller_from_mcp_context(ctx) if ctx is not None else None,
    )


async def get_research_job(job_id: str) -> Dict[str, Any]:
    """Fetch the status and result of a deferred research job.

    Statuses: `queued`/`running` (in flight), `done` (`result` and `cost_usd`
    present), `error` (`error` present), `not_found`, or `stale` — a
    queued/running job whose `updated_at` is older than
    UNIGROK_JOB_TIMEOUT_SEC, meaning the task that owned it did not survive a
    server restart and the job will never finish on its own.

    Args:
        job_id: ID returned by `submit_research_job`.
    """
    view = await get_job_manager().get(str(job_id or "").strip())
    if view is None:
        return {"status": "not_found", "job_id": job_id}
    return view


async def list_research_jobs(limit: int = 20) -> Dict[str, Any]:
    """List the most recent deferred research jobs, newest first.

    Args:
        limit: Maximum number of jobs to return (clamped to 1-100, default 20).
    """
    jobs = await get_job_manager().list(limit)
    return {"jobs": jobs, "count": len(jobs)}


def register_research_tools(mcp: FastMCP):
    # submit mutates state (creates a job) — deliberately NOT readOnly.
    mcp.add_tool(submit_research_job)
    mcp.add_tool(get_research_job, annotations=READONLY_TOOL)
    mcp.add_tool(list_research_jobs, annotations=READONLY_TOOL)
