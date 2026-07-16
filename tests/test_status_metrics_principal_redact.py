"""Bound principals must not receive cross-caller operator metrics via status."""

from __future__ import annotations

import json

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.tools.system import grok_mcp_status


@pytest.mark.asyncio
async def test_bound_principal_status_json_is_redacted(monkeypatch):
    monkeypatch.setattr(
        "src.tools.system.run_blocking",
        lambda *a, **k: {
            "state": "ready",
            "auth": "oauth_verified",
            "setup_command": "x",
            "ready": True,
            "binary": True,
        },
    )
    # run_blocking is async in real code — patch with AsyncMock style
    async def fake_run_blocking(fn, *args, timeout=None, **kwargs):
        return {
            "state": "ready",
            "auth": "oauth_verified",
            "setup_command": "x",
            "ready": True,
            "binary": True,
        }

    monkeypatch.setattr("src.tools.system.run_blocking", fake_run_blocking)
    monkeypatch.setattr(
        "src.tools.system.credential_plane_contract",
        lambda *_a, **_k: {
            "policy": "cli_first",
            "preferred_plane": "CLI",
            "effective_plane": "CLI",
            "service_usable": True,
        },
    )
    token = set_active_principal("oauth:service:tenant-a")
    try:
        raw = await grok_mcp_status(view="json")
    finally:
        reset_active_principal(token)
    payload = json.loads(raw)
    assert payload["metrics_redacted"] is True
    assert "planes" not in payload
    assert "credential_planes" in payload


@pytest.mark.asyncio
async def test_bound_principal_status_text_omits_top_callers(monkeypatch):
    async def fake_run_blocking(fn, *args, timeout=None, **kwargs):
        return {
            "state": "ready",
            "auth": "oauth_verified",
            "setup_command": "x",
            "ready": True,
            "binary": True,
        }

    monkeypatch.setattr("src.tools.system.run_blocking", fake_run_blocking)
    monkeypatch.setattr(
        "src.tools.system.credential_plane_contract",
        lambda *_a, **_k: {
            "policy": "cli_first",
            "preferred_plane": "CLI",
            "effective_plane": "CLI",
            "service_usable": True,
        },
    )
    token = set_active_principal("http:key-1")
    try:
        text = await grok_mcp_status(view="text")
    finally:
        reset_active_principal(token)
    assert "Top Callers Today" not in text
    assert "unbound local session" in text
