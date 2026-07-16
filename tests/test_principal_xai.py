"""Owner-default and principal-bound xAI API key resolution."""

from __future__ import annotations

import json
import os

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.principal_xai import (
    PrincipalXAIConfigurationError,
    active_xai_credential_source,
    default_xai_api_key,
    effective_xai_api_key,
    principal_xai_status,
    resolve_inference_credential,
    resolve_xai_api_key,
)


def test_owner_default_when_no_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.delenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", raising=False)
    token = set_active_principal(None)
    try:
        key, source = resolve_xai_api_key(environ=dict(os.environ))
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
        serialized = json.dumps(status)
        assert "xai-" not in serialized
        assert "xai-teammate" not in serialized
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


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("{", "invalid_json"),
        ("[]", "not_object"),
        ('{"oauth:a":""}', "invalid_key"),
        ('{" oauth:a":"xai-a"}', "invalid_principal"),
        ('{"oauth:a":"xai-a","oauth:a":"xai-b"}', "duplicate_principal"),
    ],
)
def test_invalid_principal_map_fails_closed_without_secret_output(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    code: str,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    monkeypatch.setenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", raw)
    token = set_active_principal("oauth:a")
    try:
        with pytest.raises(PrincipalXAIConfigurationError) as raised:
            resolve_xai_api_key()
        assert raised.value.code == code
        assert "xai-owner-default" not in str(raised.value)
        status = principal_xai_status()
        assert status == {
            "configured": False,
            "source": "configuration_error",
            "principal_kind": "oauth",
            "principal_override_available": False,
            "owner_default_configured": True,
            "principal_map_configured": True,
            "principal_map_valid": False,
            "principal_map_error": code,
            "principal_map_entries": 0,
        }
        assert active_xai_credential_source() == "configuration_error"
        assert "xai-" not in json.dumps(status)
    finally:
        reset_active_principal(token)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ('{"oauth:a":"' + ("x" * 65_536) + '"}', "too_large"),
        (
            json.dumps({f"oauth:user:{index}": f"key-{index}" for index in range(257)}),
            "too_many_entries",
        ),
    ],
    ids=("too-large", "too-many-entries"),
)
def test_principal_map_bounds_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    code: str,
) -> None:
    monkeypatch.setenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", raw)
    with pytest.raises(PrincipalXAIConfigurationError) as raised:
        resolve_xai_api_key(principal="oauth:user:1")
    assert raised.value.code == code


def test_rotation_changes_generation_but_not_principal_cache_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-owner-default")
    token = set_active_principal("oauth:github|user:42")
    try:
        monkeypatch.setenv(
            "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
            json.dumps({"oauth:github|user:42": "xai-first"}),
        )
        first = resolve_inference_credential()
        monkeypatch.setenv(
            "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
            json.dumps({"oauth:github|user:42": "xai-second"}),
        )
        second = resolve_inference_credential()
    finally:
        reset_active_principal(token)

    assert first[:3] == ("xai-first", "principal", "principal:oauth:github|user:42")
    assert second[:3] == ("xai-second", "principal", "principal:oauth:github|user:42")
    assert first[3] != second[3]
    assert "xai-first" not in first[3]
    assert "xai-second" not in second[3]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 1.0])
async def test_run_blocking_preserves_principal_context(
    timeout: float | None,
) -> None:
    from src.identity import get_active_principal
    from src.utils import run_blocking

    token = set_active_principal("oauth:github|user:42")
    try:
        assert await run_blocking(get_active_principal, timeout=timeout) == (
            "oauth:github|user:42"
        )
    finally:
        reset_active_principal(token)
