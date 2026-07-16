"""Bound principals cannot touch shared-account stateful completions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.tools.chats import (
    delete_stateful_response,
    retrieve_stateful_response,
    stateful_chat,
)


@pytest.mark.asyncio
async def test_bound_principal_cannot_use_stateful_responses():
    token = set_active_principal("oauth:service:tenant-a")
    try:
        with patch("src.tools.chats.get_xai_client") as get_client:
            with pytest.raises(PermissionError, match="bound HTTP/MCP principals"):
                await retrieve_stateful_response("resp-foreign")
            with pytest.raises(PermissionError, match="bound HTTP/MCP principals"):
                await delete_stateful_response("resp-foreign")
            blocked = await stateful_chat("hello")
            assert blocked.finish_reason == "error"
            assert "bound HTTP/MCP principals" in blocked.response
    finally:
        reset_active_principal(token)
    get_client.assert_not_called()
