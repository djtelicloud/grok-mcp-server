"""Phase 3 SRP: chats + system + media domain builders."""
from __future__ import annotations

import pytest

from unigrok_public.tools import chats, media, system


def test_healthz_body():
    body = system.healthz_body(service="UniGrok", version="9.9.9", layer="public")
    assert body["status"] == "ok"
    assert body["service"] == "UniGrok"


def test_readyz_body_full_and_cloudrun():
    body, code = system.readyz_body(
        ready=True,
        catalogs={"cli": {"ready": True}},
        bootstrap={"can_chat": True},
        state_ready=True,
        cloudrun=False,
    )
    assert code == 200 and body["status"] == "ready" and "planes" in body
    body2, code2 = system.readyz_body(
        ready=False, catalogs={}, bootstrap={}, state_ready=False, cloudrun=True
    )
    assert code2 == 503 and body2 == {"status": "not_ready"}


def test_list_models_body_shape():
    catalogs = {
        "cli": {"ready": True, "models": ["a", "b"], "default_model": "a"},
        "api": {
            "configured": True,
            "ready": True,
            "default_model": "a",
            "image_models": [{"id": "img-1"}],
        },
    }
    body = system.list_models_body(catalogs=catalogs, api_model_ids=["a", "c"])
    assert body["api"]["language_models"] == ["a", "c"]
    assert body["api"]["image_models"] == ["img-1"]
    assert "a" in body["all_model_ids"]
    assert body["shared_model_ids"] == ["a"]
    assert body["model_allowlist"] is None


def test_status_and_benchmark_bodies():
    catalogs = {
        "cli": {"ready": True},
        "api": {"ready": False},
        "local": {"ready": False, "models": []},
    }
    desc = {
        "task_rag": {},
        "bootstrap": {"can_chat": True},
        "credential_planes": {},
    }
    st = system.status_body(
        service="s",
        version="1",
        layer="public",
        tool_count=3,
        catalogs=catalogs,
        description=desc,
        state_ready=True,
        telemetry={"sample_size": 1},
        circuit_breakers={},
        metered_api_enabled=False,
        cloudrun=False,
    )
    assert st["tool_count"] == 3
    assert st["state"]["lifetime"] == "persistent_volume"
    bs = system.benchmark_status_body(telemetry={"x": 1}, circuit_breakers={})
    assert bs["semantic_evaluation"]["tool"] == "record_benchmark_result"


def test_chat_context_and_sessions():
    assert chats.build_chat_system_context() is None
    assert chats.build_chat_system_context(layer_block="L", knowledge_block="K") == "L\n\nK"
    assert chats.list_sessions_body([{"name": "a"}])["count"] == 1


def test_media_validators():
    assert media.validated_file_id("file_1") == "file_1"
    with pytest.raises(ValueError):
        media.validated_file_id("../bad")
    assert media.validated_media_url("https://example.com/a.png", "image_url").startswith(
        "https://"
    )
    with pytest.raises(ValueError):
        media.validated_media_url("http://example.com/a.png", "image_url")
    with pytest.raises(ValueError):
        media.validated_media_url("https://127.0.0.1/a.png", "image_url")
    assert media.validated_image_count(3) == 3
    with pytest.raises(ValueError):
        media.validated_image_count(0)
    assert media.validated_video_duration(None, lo=1, hi=15) is None
    assert media.validated_video_duration(5, lo=1, hi=15) == 5
    with pytest.raises(ValueError):
        media.validated_video_duration(99, lo=1, hi=15)
    assert media.validated_upload_filename("a.txt") == "a.txt"
    with pytest.raises(ValueError):
        media.validated_upload_filename("../etc/passwd")
    raw = media.decode_upload_content("YQ==", max_bytes=10)  # 'a'
    assert raw == b"a"
    media.require_confirm_delete(True, what="x")
    with pytest.raises(ValueError):
        media.require_confirm_delete(False, what="x")
