"""Contract tests for the curated, agent-grounded UniGrok FAQ."""

import json

import pytest

from src.faq import FAQDocumentError, parse_faq_document
from src.tools.faq import lookup_unigrok_faq


def test_faq_document_parses_release_versioned_entries():
    from src.faq import get_faq_index

    index = get_faq_index()

    assert index.schema_version == "1"
    assert index.source_version == "0.4.1"
    assert index.get("cursor-connect") is not None
    assert index.get("CURSOR CONNECT") is not None


def test_faq_document_rejects_entries_without_keywords():
    with pytest.raises(FAQDocumentError, match="Keywords"):
        parse_faq_document(
            """---
okf_version: "0.1"
faq_schema_version: "1"
source_version: "0.4.1"
---

## Question {#question}

Answer.
"""
        )


@pytest.mark.asyncio
async def test_agent_faq_lookup_returns_verified_cursor_context():
    result = json.loads(await lookup_unigrok_faq("How do I connect Cursor to UniGrok?"))

    assert result["source_uri"] == "grok://faq"
    assert result["count"] >= 1
    assert result["matches"][0]["id"] == "cursor-connect"
    assert ".cursor/mcp.json" in result["matches"][0]["answer_excerpt"]


@pytest.mark.asyncio
async def test_agent_faq_lookup_leaves_non_matches_for_normal_reasoning():
    result = json.loads(await lookup_unigrok_faq("Write a poem about ocean currents"))

    assert result["count"] == 0
    assert "Continue normal diagnosis" in result["next_step"]


@pytest.mark.asyncio
async def test_agent_faq_lookup_rejects_incidental_cursor_mention():
    result = json.loads(await lookup_unigrok_faq("How should I implement cursor pagination in my API?"))

    assert result["count"] == 0


@pytest.mark.asyncio
async def test_faq_lookup_is_private_to_the_agent_loop():
    from src.server import mcp

    public_tool_names = {tool.name for tool in await mcp.list_tools()}

    assert "lookup_unigrok_faq" not in public_tool_names
    assert "faq_search" not in public_tool_names
    assert "faq_get" not in public_tool_names


def test_agent_tool_schema_describes_relevance_guard():
    from src.utils import _build_custom_tools

    tool = next(item for item in _build_custom_tools() if item.function.name == "lookup_unigrok_faq")

    assert "only when the user explicitly asks" in tool.function.description
    assert "Do not use it for unrelated questions" in tool.function.description