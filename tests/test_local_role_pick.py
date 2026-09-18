# ruff: noqa
"""Local role pick: chat Gemma over function/router/embedding DMR listings."""

from __future__ import annotations

import asyncio
import inspect

from unigrok_public import server

_CATALOG = {
    "local": {
        "models": [
            "docker.io/ai/functiongemma:latest",
            "docker.io/ai/embeddinggemma:latest",
            "docker.io/ai/mxbai-embed-large:latest",
            "docker.io/ai/gemma3-qat:latest",
            "docker.io/ai/gemma3n:latest",
            "docker.io/unigrok/needle-26m:latest",
            "docker.io/unigrok/gemma3n-router-round3:q8_0",
            "docker.io/unigrok/router-e2b-r2:q8_0",
            "docker.io/ai/nemotron-3-nano:latest",
        ],
        "default_model": "docker.io/ai/functiongemma:latest",
    }
}


def test_local_chat_timeout_is_not_the_build_deadline() -> None:
    assert server.LOCAL_CHAT_TIMEOUT_SECONDS == 20
    assert server.LOCAL_CHAT_TIMEOUT_SECONDS < server.BUILD_TIMEOUT_SECONDS
    src = inspect.getsource(server._local_chat)
    assert "timeout=LOCAL_CHAT_TIMEOUT_SECONDS" in src
    assert "timeout=BUILD_TIMEOUT_SECONDS" not in src


def test_text_score_prefers_gemma3_qat_over_functiongemma() -> None:
    qat = server._local_role_model_score("docker.io/ai/gemma3-qat:latest", "text_generator")
    fn = server._local_role_model_score("docker.io/ai/functiongemma:latest", "text_generator")
    router = server._local_role_model_score(
        "docker.io/unigrok/gemma3n-router-round3:q8_0", "text_generator"
    )
    embed = server._local_role_model_score(
        "docker.io/ai/embeddinggemma:latest", "text_generator"
    )
    assert qat < fn
    assert qat < router
    assert qat < embed


def test_router_score_prefers_named_house_router() -> None:
    house = server._local_role_model_score(
        "docker.io/unigrok/gemma3n-router-round3:q8_0", "router"
    )
    fn = server._local_role_model_score("docker.io/ai/functiongemma:latest", "router")
    qat = server._local_role_model_score("docker.io/ai/gemma3-qat:latest", "router")
    assert house < fn
    assert house < qat


def test_preferred_env_wins() -> None:
    preferred = "docker.io/ai/gemma3n:latest"
    gemma3n = server._local_role_model_score(
        preferred, "text_generator", preferred=preferred
    )
    qat = server._local_role_model_score(
        "docker.io/ai/gemma3-qat:latest", "text_generator", preferred=preferred
    )
    assert gemma3n < qat


class _AllGemmaBound:
    async def local_bind(self, model_id: str, role: str):
        low = model_id.lower()
        if "mxbai" in low or "needle" in low or "nemotron" in low:
            return None
        return {"metric_id": f"{role}:gemma:offline-min"}


def test_pick_text_generator_skips_functiongemma(monkeypatch) -> None:
    monkeypatch.setattr(server, "STATE", _AllGemmaBound())
    picked = asyncio.run(server._pick_local_role_model("text_generator", _CATALOG))
    assert picked == "docker.io/ai/gemma3-qat:latest"


def test_pick_router_uses_house_router(monkeypatch) -> None:
    monkeypatch.setattr(server, "STATE", _AllGemmaBound())
    picked = asyncio.run(server._pick_local_role_model("router", _CATALOG))
    assert picked == "docker.io/unigrok/gemma3n-router-round3:q8_0"


def test_pick_honors_preferred_model(monkeypatch) -> None:
    monkeypatch.setattr(server, "STATE", _AllGemmaBound())
    monkeypatch.setattr(server, "LOCAL_PREFERRED_MODEL", "docker.io/ai/gemma3n:latest")
    picked = asyncio.run(server._pick_local_role_model("text_generator", _CATALOG))
    assert picked == "docker.io/ai/gemma3n:latest"
