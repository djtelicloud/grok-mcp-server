"""Owner-default xAI inference key allowlist fall-through (factory patch)."""

from __future__ import annotations

from unigrok_public.principal_xai import resolve_xai_api_key


def test_falls_through_empty_primary_to_sky_inference() -> None:
    env = {
        "XAI_API_KEY": "",
        "XAI_API_KEY_SKY_INFERENCE": "xai-live-sky",
        "XAI_API_KEY_GROUND": "xai-other",
    }
    key, source = resolve_xai_api_key(principal=None, environ=env)
    assert key == "xai-live-sky"
    assert source == "owner_default:XAI_API_KEY_SKY_INFERENCE"


def test_preferred_empty_falls_through_to_xai_api_key() -> None:
    env = {
        "XAI_PLANE_API": "XAI_API_KEY_GROUND",
        "XAI_API_KEY_GROUND": "",
        "XAI_API_KEY": "xai-main",
    }
    key, source = resolve_xai_api_key(principal=None, environ=env)
    assert key == "xai-main"
    assert source == "owner_default:XAI_API_KEY"


def test_skips_cursor_tokens() -> None:
    env = {
        "XAI_API_KEY": "",
        "XAI_API_KEY_CURSOR_SKY": "crsr_not_valid_here",
        "XAI_API_KEY_GROUND": "xai-ground-ok",
    }
    key, source = resolve_xai_api_key(principal=None, environ=env)
    assert key == "xai-ground-ok"
    assert source == "owner_default:XAI_API_KEY_GROUND"
