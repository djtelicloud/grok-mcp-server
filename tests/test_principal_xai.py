"""Owner-default and principal-bound xAI API key resolution."""

from __future__ import annotations

import json

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.principal_xai import (
    default_xai_api_key,
    effective_xai_api_key,
    principal_xai_status,
    resolve_xai_api_key,
)


def test_owner_default_when_no_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.delenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", raising=False)
    token = set_active_principal(None)
    try:
        key, source = resolve_xai_api_key(environ=dict(**__import__("os").environ))
        assert key == "xai-owner-default"
        assert source == "owner_default"
    finally:
        reset_active_principal(token)


def test_oauth_principal_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({"oauth:github|user:42": "xai-teammate"}),
    )
    token = set_active_principal("oauth:github|user:42")
    try:
        key, source = resolve_xai_api_key()
        assert key == "xai-teammate"
        assert source == "principal"
        status = principal_xai_status()
        assert status["source"] == "principal"
        assert status["principal_override_available"] is True
        assert "xai-" not in json.dumps(status) or status["configured"] is True
        # Status must not embed the secret value.
        assert "xai-teammate" not in json.dumps(status)
    finally:
        reset_active_principal(token)


def test_oauth_principal_bare_map_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({"github|user:99": "xai-bare"}),
    )
    token = set_active_principal("oauth:github|user:99")
    try:
        assert effective_xai_api_key() == "xai-bare"
    finally:
        reset_active_principal(token)


def test_unknown_oauth_falls_back_to_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({"oauth:other": "xai-other"}),
    )
    token = set_active_principal("oauth:github|user:nope")
    try:
        key, source = resolve_xai_api_key()
        assert key == "xai-owner-default"
        assert source == "owner_default"
    finally:
        reset_active_principal(token)


def test_anon_ignores_principal_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({"http:anon": "xai-should-not-use", "oauth:x": "xai-x"}),
    )
    token = set_active_principal("http:anon")
    try:
        key, source = resolve_xai_api_key()
        assert key == "xai-owner-default"
        assert source == "owner_default"
    finally:
        reset_active_principal(token)


def test_client_id_cannot_select_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Client-ID is not consulted; only OAuth principal map entries apply."""
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({"cursor": "xai-spoof", "oauth:real": "xai-real"}),
    )
    token = set_active_principal("oauth:real")
    try:
        assert effective_xai_api_key() == "xai-real"
    finally:
        reset_active_principal(token)
    # Without principal, spoof label in map is irrelevant.
    assert default_xai_api_key() == "xai-owner-default"
