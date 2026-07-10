"""Private, deterministic FAQ retrieval for the UniGrok agent loop.

This is intentionally an internal tool, not a public MCP command. Grok decides
whether the user's request is a UniGrok support question worth grounding in the
curated FAQ; no server-side keyword router or automatic response is involved.
"""

from __future__ import annotations

import json

from ..faq import FAQDocumentError, FAQ_SOURCE_URI, get_faq_index
from ..utils import register_internal_tool

_MAX_SEARCH_LIMIT = 10


async def lookup_unigrok_faq(query: str, limit: int = 3) -> str:
    """Retrieve verified FAQ context only for an explicit UniGrok help request.

    This local, zero-cost lookup never calls xAI or reads user/session memory.
    Use it when the user asks about UniGrok setup, IDE configuration, routing,
    security, health checks, or troubleshooting. Do not call it for a general
    question that merely happens to mention a word such as "port" or "Cursor".
    If no entry applies, continue with normal reasoning instead of forcing an
    FAQ answer.

    Args:
        query: The user's clearly UniGrok-specific support question.
        limit: Maximum matches to return (1-10, default 3).
    """
    try:
        bounded_limit = max(1, min(int(limit), _MAX_SEARCH_LIMIT))
    except (TypeError, ValueError):
        bounded_limit = 3
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return json.dumps(
            {
                "schema_version": "1",
                "status": "invalid_query",
                "matches": [],
                "source_uri": FAQ_SOURCE_URI,
            },
            sort_keys=True,
        )

    try:
        index = get_faq_index()
    except FAQDocumentError:
        return json.dumps(
            {
                "schema_version": "1",
                "status": "faq_unavailable",
                "source_uri": FAQ_SOURCE_URI,
                "next_step": "Use the project documentation or report the missing FAQ bundle.",
            },
            sort_keys=True,
        )

    matches = index.search(normalized_query, bounded_limit)
    data = {
        "schema_version": index.schema_version,
        "source_version": index.source_version,
        "query": normalized_query,
        "count": len(matches),
        "matches": matches,
        "source_uri": FAQ_SOURCE_URI,
    }
    if not matches:
        data["next_step"] = (
            "No curated FAQ entry matched. Continue normal diagnosis; do not present an FAQ answer."
        )
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


register_internal_tool("lookup_unigrok_faq", lookup_unigrok_faq)
