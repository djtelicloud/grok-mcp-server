# src/tools/knowledge.py
# Local-first knowledge memory tools: durable distilled FACTS (knowledge
# table in the shared GrokSessionStore), not transcripts. The local store is
# the source of truth and works everywhere; UNIGROK_COLLECTIONS=1 optionally
# mirrors facts into an xAI collection and merges collection matches into
# search results (capability-gated, best-effort — see the adapter in
# src/utils.py).

import logging
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ..identity import caller_from_mcp_context, scoped_session
from ..jobs import get_job_manager
from ..utils import (
    _normalize_fact_scope,
    search_knowledge_collection,
    store,
    sync_fact_to_collection,
)

logger = logging.getLogger("GrokMCP")

READONLY_TOOL = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE_TOOL = ToolAnnotations(destructiveHint=True)


def _fact_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """Public view of a knowledge row (drops the internal terms column)."""
    view = {
        "id": row.get("id"),
        "fact": row.get("fact"),
        "scope": row.get("scope"),
        "source": row.get("source"),
        "created_at": row.get("created_at"),
        "last_used_at": row.get("last_used_at"),
        "uses": row.get("uses"),
    }
    if "score" in row:
        view["score"] = row.get("score")
    return view


async def remember_fact(fact: str, scope: str = "global") -> Dict[str, Any]:
    """Save one durable fact to the local workspace knowledge memory.

    Facts are distilled knowledge — decisions, constraints, preferences,
    verified findings — injected as hints into future prompts that match
    them. Saving an identical fact again touches the existing row instead of
    duplicating it.

    Args:
        fact: One self-contained sentence with concrete specifics.
        scope: 'global' (default, injected everywhere) or a session name for
            session-scoped knowledge.
    """
    text = str(fact or "").strip()
    if not text:
        return {"error": "Input Validation Error: fact must not be empty."}
    # Normalize the client-controlled scope once (redacted + bounded — see
    # _normalize_fact_scope) so the stored row, the cloud mirror, and the
    # echoed result all agree on the same value.
    scope_value = _normalize_fact_scope(scope)
    fact_id = await store.save_fact(text, scope=scope_value, source="manual")
    if fact_id is None:
        return {"error": "Input Validation Error: fact must not be empty."}
    # Best-effort cloud mirror — a no-op unless UNIGROK_COLLECTIONS=1 and
    # the installed SDK exposes the collections service.
    synced = await sync_fact_to_collection(fact_id, text, scope=scope_value, source="manual")
    result = {"fact_id": fact_id, "scope": scope_value, "status": "saved"}
    if synced:
        result["collection_synced"] = True
    return result


async def search_knowledge(query: str, limit: int = 5) -> Dict[str, Any]:
    """Search the workspace knowledge memory for facts matching a query.

    Local results are ranked by FTS5 bm25 when available (term-overlap
    otherwise). With UNIGROK_COLLECTIONS=1 and a capable SDK, matches from
    the xAI knowledge collection are merged in (origin='collection').

    Args:
        query: Search terms.
        limit: Maximum number of local facts to return (1-25, default 5).
    """
    text = str(query or "").strip()
    if not text:
        return {"error": "Input Validation Error: query must not be empty."}
    facts = await store.search_facts(text, scope=None, limit=limit)
    results = [_fact_view(row) for row in facts]
    # Collection passthrough: best-effort, never raises, [] when disabled.
    remote = await search_knowledge_collection(text, limit=limit)
    local_texts = {str(item.get("fact") or "").strip() for item in results}
    for item in remote:
        if str(item.get("fact") or "").strip() not in local_texts:
            results.append(item)
    return {"facts": results, "count": len(results)}


async def forget_fact(fact_id: int) -> Dict[str, Any]:
    """Permanently delete one fact from the workspace knowledge memory.

    Args:
        fact_id: The id returned by `remember_fact` or `search_knowledge`.
    """
    try:
        target = int(fact_id)
    except (TypeError, ValueError):
        return {"error": "Input Validation Error: fact_id must be an integer."}
    deleted = await store.delete_fact(target)
    return {"fact_id": target, "status": "deleted" if deleted else "not_found"}


async def distill_session(session: str, ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Distill a chat session's stored history into durable knowledge facts.

    Submits a background job (same lifecycle as research jobs — poll
    `get_research_job(job_id)`) that summarizes the session into 3-8
    standalone facts on the cheap coding model and saves them to the
    knowledge memory with source='session:<name>'.

    Args:
        session: Name of a stored chat session.
    """
    name = str(session or "").strip()
    if not name:
        return {"error": "Input Validation Error: session must not be empty."}
    # Match agent/chat: namespace by principal + X-Client-ID so short names
    # resolve to the caller's stored history and foreign fully-qualified
    # session ids cannot be distilled across client labels.
    name = scoped_session(name) or name
    # ctx is FastMCP-injected (hidden from the tool schema): the clientInfo
    # name identifies which agent submitted the job on the persisted row —
    # same attribution as submit_research_job.
    return await get_job_manager().submit_distill(
        name, caller=caller_from_mcp_context(ctx) if ctx is not None else None
    )


def register_knowledge_tools(mcp: FastMCP):
    # remember/distill mutate state (fact rows / job rows) — NOT readOnly.
    mcp.add_tool(remember_fact)
    mcp.add_tool(search_knowledge, annotations=READONLY_TOOL)
    mcp.add_tool(forget_fact, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(distill_session)
