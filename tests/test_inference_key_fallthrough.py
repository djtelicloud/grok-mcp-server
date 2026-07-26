"""Public owner-default xAI inference-key boundary."""

from __future__ import annotations

import pytest

from unigrok_public import principal_xai
from unigrok_public.principal_xai import resolve_xai_api_key


def test_resolves_only_canonical_owner_key() -> None:
    key, source = resolve_xai_api_key(
        principal=None,
        environ={"XAI_API_KEY": "xai-public-owner"},
    )

    assert key == "xai-public-owner"
    assert source == "owner_default:XAI_API_KEY"


@pytest.mark.parametrize(
    "alias",
    (
        "XAI_API_KEY_SKY_INFERENCE",
        "XAI_API_KEY_GROUND",
        "XAI_API_KEY_UNIGROK_GROUND",
        "XAI_API_KEY_CURSOR_SKY",
        "XAI_MANAGEMENT_API_KEY",
    ),
)
def test_non_public_owner_slots_are_ignored(alias: str) -> None:
    key, source = resolve_xai_api_key(
        principal=None,
        environ={alias: "xai-not-public"},
    )

    assert key == ""
    assert source == "owner_default"


def test_preference_variable_cannot_redirect_owner_key() -> None:
    key, source = resolve_xai_api_key(
        principal=None,
        environ={
            "XAI_PLANE_API": "XAI_API_KEY_GROUND",
            "XAI_API_KEY_GROUND": "xai-private-alias",
            "XAI_API_KEY": "xai-public-owner",
        },
    )

    assert key == "xai-public-owner"
    assert source == "owner_default:XAI_API_KEY"


@pytest.mark.parametrize("value", ("crsr_not_valid_here", "management-token"))
def test_canonical_slot_rejects_non_inference_material(value: str) -> None:
    assert resolve_xai_api_key(
        principal=None,
        environ={"XAI_API_KEY": value},
    ) == ("", "owner_default")


@pytest.mark.parametrize(
    "source",
    (
        "owner_default",
        "owner_default:XAI_API_KEY",
    ),
)
def test_owner_sources_use_shared_generation_slot(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    active = "oauth:issuer:alice"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(principal_xai, "get_active_principal", lambda: active)
    monkeypatch.setattr(
        principal_xai,
        "resolve_xai_api_key",
        lambda **_kwargs: ("owner-test-key", source),
    )
    monkeypatch.setattr(
        principal_xai,
        "_generation",
        lambda slot, key: calls.append((slot, key)) or "test-generation",
    )

    assert principal_xai.resolve_inference_credential() == (
        "owner-test-key",
        source,
        "test-generation",
    )
    assert calls == [("owner_default", "owner-test-key")]
