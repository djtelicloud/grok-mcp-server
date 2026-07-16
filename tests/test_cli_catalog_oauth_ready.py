"""CLI catalog availability must require the same OAuth predicate as plane ready."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.utils import discover_grok_cli_models


@pytest.mark.asyncio
async def test_catalog_unavailable_without_oauth_verified(monkeypatch):
    monkeypatch.setattr("src.utils.is_cloudrun_runtime", lambda: False)
    monkeypatch.setattr(
        "src.utils.PathResolver.get_grok_cli_path",
        staticmethod(lambda: "grok"),
    )
    monkeypatch.setattr("src.utils.grok_cli_oauth_env", lambda: {})

    class FakeProc:
        returncode = 0

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def fake_create(*_a, **_k):
        return FakeProc()

    async def fake_communicate(proc, timeout_sec, input_data=None):
        body = (
            b"Available models:\n"
            b"grok-4.5 (default)\n"
            b"grok-composer-2.5-fast\n"
        )
        return body, b""

    monkeypatch.setattr(
        "src.utils.asyncio.create_subprocess_exec", fake_create
    )
    monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

    result = await discover_grok_cli_models(timeout_sec=1.0)
    assert result["available"] is False
    assert any("verified grok.com OAuth" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_catalog_available_with_oauth_verified(monkeypatch):
    monkeypatch.setattr("src.utils.is_cloudrun_runtime", lambda: False)
    monkeypatch.setattr(
        "src.utils.PathResolver.get_grok_cli_path",
        staticmethod(lambda: "grok"),
    )
    monkeypatch.setattr("src.utils.grok_cli_oauth_env", lambda: {})

    class FakeProc:
        returncode = 0

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def fake_create(*_a, **_k):
        return FakeProc()

    async def fake_communicate(proc, timeout_sec, input_data=None):
        body = (
            b"Logged in with grok.com\n"
            b"Available models:\n"
            b"grok-4.5 (default)\n"
            b"grok-composer-2.5-fast\n"
        )
        return body, b""

    monkeypatch.setattr(
        "src.utils.asyncio.create_subprocess_exec", fake_create
    )
    monkeypatch.setattr("src.utils.communicate_with_timeout", fake_communicate)

    result = await discover_grok_cli_models(timeout_sec=1.0)
    assert result["available"] is True
