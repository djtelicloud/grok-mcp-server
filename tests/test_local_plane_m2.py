"""GemmaGrok Local-Plane M2 — Cost-aware local routing, budget fallback & boundary suite."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from unigrok_public import caller_budget, gemmagrok_peer, local_plane_loader, server, xai_api
from unigrok_public.caller_budget import CallerBudgetExceeded
from unigrok_public.state import PublicStateStore


def _ready_api_and_local_catalogs() -> dict[str, Any]:
    return {
        "cli": {
            "ready": False,
            "binary": False,
            "authenticated": False,
            "models": [],
            "default_model": None,
        },
        "api": {
            "configured": True,
            "ready": True,
            "models": [{"id": "grok-4.20"}, {"id": "grok-4-fast"}],
            "image_models": [],
            "default_model": "grok-4.20",
        },
        "local": {
            "configured": True,
            "ready": True,
            "runtime_up": True,
            "data_ready": True,
            "models": ["ai/gemma3-qat:4B", "ai/functiongemma:latest"],
            "default_model": "ai/gemma3-qat:4B",
            "router_models": ["ai/functiongemma:latest"],
            "runtime_kind": "openai_compat",
        },
    }


@pytest.mark.asyncio
async def test_auto_cross_plane_budget_exhausted_serves_local_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1. plane='auto' + fallback_policy='cross_plane' with ready local and API budget
    exceeded serves on local with degraded=True, trigger='budget_exhausted',
    reason='api_budget_exceeded', and zero post-guard API calls.
    """
    catalogs = _ready_api_and_local_catalogs()

    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return catalogs

    api_calls: list[str] = []

    async def forbidden_api_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
        api_calls.append(str(args or kwargs))
        raise AssertionError("xai_api.chat must never be called after budget guard fires")

    async def fake_enforce_budget(_store: Any) -> None:
        raise CallerBudgetExceeded(
            "Caller daily budget of $1.00 USD reached ($1.00 USD recorded today)"
        )

    async def fake_local_slot_acquire() -> bool:
        return True

    async def fake_serve_local_offline(
        prompt: str,
        *,
        system_context: str | None = None,
        prior_continue_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "text": f"local-degraded-answer:{prompt}",
            "model": "ai/gemma3-qat:4B",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
            "requested_plane": "auto",
            "resolved_plane": "local",
            "effective_plane": "local",
            "fallback_policy": "cross_plane",
            "fallback_occurred": False,
            "fallback_from": None,
            "fallback_reason": None,
            "degraded": True,
            "trigger": "none",
            "continue_count": prior_continue_count,
            "memory_used": bool(system_context),
            "orchestration": {
                "lead": "ai/functiongemma:latest",
                "route": "direct",
                "specialist_model": "ai/gemma3-qat:4B",
                "brief_authored_by_lead": True,
                "router_source": "local_router_floor",
                "brief_source": "local_router_floor",
            },
        }

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    monkeypatch.setattr(server, "enforce_caller_budget", fake_enforce_budget)
    monkeypatch.setattr(server, "_local_slot_acquire", fake_local_slot_acquire)
    monkeypatch.setattr(server, "_serve_local_offline", fake_serve_local_offline)
    monkeypatch.setattr(xai_api, "chat", forbidden_api_chat)

    result = await server._run_unified(
        "Explain local M2 budget fallback.",
        model=None,
        effort="low",
        plane="auto",
        fallback_policy="cross_plane",
        agentic=False,
        max_turns=1,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
    )

    assert api_calls == []
    assert result["resolved_plane"] == "local"
    assert result["effective_plane"] == "local"
    assert result["degraded"] is True
    assert result["trigger"] == "budget_exhausted"
    assert result["fallback_reason"] == "api_budget_exceeded"
    assert result["fallback_from"] == "api"
    assert result["fallback_occurred"] is True
    assert result["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_explicit_api_budget_exceeded_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2. Explicit plane='api' raises/blocks on CallerBudgetExceeded; local loader is never called."""
    catalogs = _ready_api_and_local_catalogs()

    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return catalogs

    local_called = False

    async def forbidden_local_offline(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal local_called
        local_called = True
        raise AssertionError("local plane must never be called when plane='api' is explicit")

    async def fake_enforce_budget(_store: Any) -> None:
        raise CallerBudgetExceeded("Caller daily budget exhausted")

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    monkeypatch.setattr(server, "enforce_caller_budget", fake_enforce_budget)
    monkeypatch.setattr(server, "_serve_local_offline", forbidden_local_offline)

    with pytest.raises(CallerBudgetExceeded, match="Caller daily budget exhausted"):
        await server._run_unified(
            "Explicit API call under exhausted budget",
            model=None,
            effort="low",
            plane="api",
            fallback_policy="cross_plane",
            agentic=False,
            max_turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
        )

    assert local_called is False


@pytest.mark.asyncio
async def test_same_plane_budget_exceeded_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3. plane='auto' + fallback_policy='same_plane' fails closed on CallerBudgetExceeded
    without crossing to local.
    """
    catalogs = _ready_api_and_local_catalogs()

    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return catalogs

    local_called = False

    async def forbidden_local_offline(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal local_called
        local_called = True
        raise AssertionError("same_plane policy must never cross to local")

    async def fake_enforce_budget(_store: Any) -> None:
        raise CallerBudgetExceeded("Local daily spend guard declined the requested reservation")

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    monkeypatch.setattr(server, "enforce_caller_budget", fake_enforce_budget)
    monkeypatch.setattr(server, "_serve_local_offline", forbidden_local_offline)

    with pytest.raises(CallerBudgetExceeded, match="spend guard declined"):
        await server._run_unified(
            "Auto plane with same_plane fallback policy",
            model=None,
            effort="low",
            plane="auto",
            fallback_policy="same_plane",
            agentic=False,
            max_turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
        )

    assert local_called is False


@pytest.mark.asyncio
async def test_route_floor_zero_cost_when_api_unready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4. When _heuristic_route(prompt) is None and API is unready/budget-exceeded
    while local is ready, _route_task uses the zero-cost local router floor
    (route in {'direct', 'code', 'image', 'video'}) without opening a remote API
    client or charging metered budget.
    """
    prompt = "Implement and refactor a Python parser module for streaming JSON."
    assert server._heuristic_route(prompt) is None

    catalogs = _ready_api_and_local_catalogs()
    catalogs["api"]["ready"] = False

    api_called = False

    async def forbidden_api_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal api_called
        api_called = True
        raise AssertionError("xai_api.chat must not be called when API is unready")

    async def fake_local_router_floor(
        task_prompt: str, *, system_context: str | None = None
    ) -> dict[str, Any]:
        del system_context
        return {
            "route": "code",
            "brief": f"Write clean Python parser module for: {task_prompt[:40]}",
            "router_model": "ai/functiongemma:latest",
        }

    monkeypatch.setattr(xai_api, "chat", forbidden_api_chat)
    monkeypatch.setattr(server, "_local_router_floor", fake_local_router_floor)

    routed = await server._route_task(prompt, catalogs)
    assert api_called is False
    assert routed["route"] in {"direct", "code", "image", "video"}
    assert routed["route"] == "code"
    assert routed["router_cost_usd"] == 0.0
    assert routed["router_source"] == "local_router_floor"
    assert routed["router_plane"] == "local"

    # Also verify when API is ready in catalog but caller budget is exhausted:
    catalogs["api"]["ready"] = True

    async def fake_budget_exhausted(_store: Any) -> dict[str, Any]:
        return {
            "exhausted": True,
            "reason": "local_daily_budget_exhausted",
            "source": "local_spend_guard",
        }

    monkeypatch.setattr(server, "inspect_caller_budget_pressure", fake_budget_exhausted)
    routed_budget = await server._route_task(prompt, catalogs)
    assert api_called is False
    assert routed_budget["route"] in {"direct", "code", "image", "video"}
    assert routed_budget["router_cost_usd"] == 0.0
    assert routed_budget["router_source"] == "local_router_floor"


@pytest.mark.asyncio
async def test_public_boundary_no_remote_no_private_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5. Peer bind set <= {'router', 'text_generator', 'code'}, session-scoped
    memory recall ('memory_used' boolean in receipt), and public boundary invariants.
    """
    allowed_roles = {"router", "text_generator", "code"}
    assert local_plane_loader.M2_PEER_ALLOWED_ROLES <= allowed_roles
    assert gemmagrok_peer.PEER_ALLOWED_ROLES <= allowed_roles

    # Verify dogfood binds in SQLite never exceed {'router', 'text_generator', 'code'}
    store = PublicStateStore(tmp_path / "m2-state.sqlite3")
    await store.health()
    rewrite = await store.rewrite_local_binds(
        [
            {
                "model_id": "ai/gemma3-qat:4B",
                "raw_name": "ai/gemma3-qat:4B",
                "runtime": "openai_compat",
                "adapters": [],
            }
        ]
    )
    bound_roles = {str(b["role"]) for b in rewrite["binds"]}
    assert bound_roles <= allowed_roles

    # Verify public server source never imports or references gemmagrok_peer/GEMMAGROK
    server_source = inspect.getsource(server)
    assert "gemmagrok_peer" not in server_source
    assert "GEMMAGROK" not in server_source
    assert "gemmagrok" not in server.PUBLIC_TOOL_NAMES

    # Verify session-scoped memory recall stamps memory_used boolean in gemmagrok_peer.chat
    monkeypatch.setenv("GEMMAGROK_STATE_DIR", str(tmp_path / "peer_sessions"))
    monkeypatch.setenv("GEMMAGROK_MODEL_ID", "gemma-3-4b-it")

    async def fake_runtime_request(
        method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del method, payload
        if path == "/v1/models":
            return {"data": [{"id": "gemma-3-4b-it"}]}
        return {
            "choices": [
                {
                    "message": {"content": "remembered session response"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(gemmagrok_peer, "_runtime_request", fake_runtime_request)

    first = await gemmagrok_peer.chat(
        "store TEAM_GOAL=ship_m2",
        session="m2-session",
    )
    assert first["memory_used"] is False
    assert first["remote_fallback"] is False

    second = await gemmagrok_peer.chat(
        "What is TEAM_GOAL?",
        session="m2-session",
    )
    assert second["memory_used"] is True
    assert second["remote_fallback"] is False
    assert second["cost_usd"] == 0.0
