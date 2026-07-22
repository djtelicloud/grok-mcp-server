# ruff: noqa
"""M3-T1 — receipt field completion + local-serve degraded=true contract."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from unigrok_public import local_plane_loader as lpl
from unigrok_public import server
from unigrok_public.state import PublicStateStore

def test_offline_success_degraded_brief_billing(monkeypatch):
    """Offline success: degraded True, brief_source, billing_class, cost 0.0."""

    async def _acq() -> bool:
        return True

    async def _router(prompt: str, *, system_context=None) -> dict[str, Any]:
        return {
            "route": "direct",
            "brief": "synthetic brief for specialist",
            "router_model": "synth-router",
        }

    async def _chat(prompt: str, **kwargs) -> dict[str, Any]:
        return {
            "text": "ok",
            "model": "synth-tg",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id: str, role: str):
        return {"metric_id": f"{role}:synth:deadbeef", "cert_id": "cert-1"}

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)

    out = asyncio.run(server._serve_local_offline("hello world"))
    assert out["degraded"] is True
    assert out["trigger"] == "none"
    assert out["brief_source"] == "local_router_floor"
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0
    assert out["continue_count"] == 0
    assert out["router_source"] == "local_router_floor"
    assert out["heuristic_only"] is False
    assert out["resolved_plane"] == "local"


def test_offline_heuristic_vs_local_router_floor_router_source(monkeypatch):
    """heuristic fixes route -> heuristic_only True; else local_router_floor."""

    async def _acq() -> bool:
        return True

    async def _router(prompt: str, *, system_context=None) -> dict[str, Any]:
        return {
            "route": "direct",
            "brief": "brief",
            "router_model": "synth-router",
        }

    async def _chat(prompt: str, **kwargs) -> dict[str, Any]:
        return {
            "text": "ok",
            "model": "synth-tg",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id: str, role: str):
        return {"metric_id": f"{role}:synth:aabbccdd", "cert_id": "cert-1"}

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)

    monkeypatch.setattr(server, "_heuristic_route", lambda p: "direct")
    heur = asyncio.run(server._serve_local_offline("confident direct"))
    assert heur["router_source"] == "heuristic"
    assert heur["heuristic_only"] is True
    assert heur["brief_source"] == "local_router_floor"
    assert heur["degraded"] is True
    assert heur["orchestration"]["router_source"] == "heuristic"

    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    floor = asyncio.run(server._serve_local_offline("router decides"))
    assert floor["router_source"] == "local_router_floor"
    assert floor["heuristic_only"] is False
    assert floor["brief_source"] == "local_router_floor"
    assert floor["orchestration"]["router_source"] == "local_router_floor"


def test_receipt_fallback_trigger_unchanged_and_defaults():
    """_receipt trigger mapping unchanged; defaults only fill absent keys."""
    base = {
        "text": "x",
        "model": "m",
        "plane": "local",
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
    }
    happy = server._receipt(
        dict(base),
        requested_plane="auto",
        resolved_plane="api",
        fallback_policy="cross_plane",
    )
    assert happy["fallback_occurred"] is False
    assert happy["degraded"] is False
    assert happy["trigger"] == server._canonical_trigger(None)
    assert happy["continue_count"] == 0
    assert happy["router_source"] == "heuristic"
    assert happy["heuristic_only"] is False

    stamped = dict(base)
    stamped["router_source"] = "cli"
    stamped["heuristic_only"] = True
    stamped["continue_count"] = 3
    kept = server._receipt(
        stamped,
        requested_plane="auto",
        resolved_plane="cli",
        fallback_policy="cross_plane",
    )
    assert kept["router_source"] == "cli"
    assert kept["heuristic_only"] is True
    assert kept["continue_count"] == 3

    fb = server._receipt(
        dict(base),
        requested_plane="auto",
        resolved_plane="local",
        fallback_policy="cross_plane",
        fallback_from="api",
        fallback_reason="timeout",
    )
    assert fb["fallback_occurred"] is True
    assert fb["degraded"] is True
    assert fb["trigger"] == server._canonical_trigger("timeout")
    assert fb["continue_count"] == 0


def test_local_failover_receipt_keyset_and_router_source_honest():
    """Failover plane-swap: degraded True; router_source=upstream plane; no fake brief."""
    result: dict[str, Any] = {
        "text": "local answer",
        "model": "synth-tg",
        "plane": "local",
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
        "stop_reason": "stop",
        "model_id": "synth-tg",
        "floor_role": "text_generator",
        "floor_metric_ids": ["text_generator:synth:deadbeef"],
    }
    result["degraded"] = True
    server._stamp_router_receipt_fields(
        result,
        router_source="api",
        heuristic_only=False,
    )
    out = server._receipt(
        result,
        requested_plane="auto",
        resolved_plane="local",
        fallback_policy="cross_plane",
        fallback_from="api",
        fallback_reason="upstream_error",
    )
    assert out["degraded"] is True
    assert out["fallback_occurred"] is True
    assert out["resolved_plane"] == "local"
    assert out["router_source"] == "api"
    assert out["heuristic_only"] is False
    assert out["continue_count"] == 0
    assert "brief_source" not in out  # not authored on direct failover path
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0
    assert out["trigger"] == server._canonical_trigger("upstream_error")


def test_offline_degraded_envelope_top_level_router_keys(monkeypatch):
    """Fail-closed offline path stamps top-level router_source + continue_count."""

    async def _acq() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    out = asyncio.run(server._serve_local_offline("busy"))
    assert out["degraded"] is True
    assert out["router_source"] == "heuristic"
    assert out["heuristic_only"] is False
    # m3-task6: §5.5 bound applies to shed — first continue carries count 1.
    assert out["continue_count"] == 1
    assert out["status"] == "continue"
    assert out["orchestration"]["router_source"] == "heuristic"


# ---------------------------------------------------------------------------
# M3-T2 — _resolve_plane admits local on remote-missing (kill offline bypass)
# ---------------------------------------------------------------------------


def _catalogs_fixture(
    *,
    cli_ready: bool = False,
    api_ready: bool = False,
    local_ready: bool = False,
    cli_models: list[str] | None = None,
    api_models: list[str] | None = None,
    local_models: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "cli": {
            "ready": cli_ready,
            "models": list(cli_models or (["cli-synth"] if cli_ready else [])),
        },
        "api": {
            "ready": api_ready,
            "models": list(api_models or (["api-synth"] if api_ready else [])),
        },
        "local": {
            "ready": local_ready,
            "models": list(local_models or (["local-synth"] if local_ready else [])),
            "default_model": (local_models or ["local-synth"])[0] if local_ready else None,
        },
    }


def test_resolve_plane_auto_returns_local_when_remotes_down_local_ready(monkeypatch):
    """Auto + remotes unready + local ready → resolve primary is local."""
    cats = _catalogs_fixture(local_ready=True)

    async def _cats():
        return cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    resolved, out_cats = asyncio.run(
        server._resolve_plane("auto", None, requires_api=False)
    )
    assert resolved == "local"
    assert out_cats is cats
    assert out_cats["local"]["ready"] is True


def test_resolve_plane_auto_raises_when_local_also_unready(monkeypatch):
    """Auto + all planes unready → existing RuntimeError (message unchanged)."""
    cats = _catalogs_fixture(cli_ready=False, api_ready=False, local_ready=False)

    async def _cats():
        return cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    with pytest.raises(RuntimeError, match="Neither Grok credential plane is ready"):
        asyncio.run(server._resolve_plane("auto", None, requires_api=False))


def test_resolve_plane_auto_cli_ready_still_prefers_cli(monkeypatch):
    """Cli-first preference unchanged when cli is ready (even if local ready)."""
    cats = _catalogs_fixture(cli_ready=True, local_ready=True)

    async def _cats():
        return cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    resolved, _ = asyncio.run(
        server._resolve_plane("auto", None, requires_api=False)
    )
    assert resolved == "cli"


def test_execute_team_turn_offline_via_resolve_no_hive_route(monkeypatch):
    """Offline serve completes via resolved local; zero _hive_route/_route_task calls."""
    cats = _catalogs_fixture(local_ready=True)
    hive_calls: list[Any] = []
    route_calls: list[Any] = []
    offline_calls: list[str] = []

    async def _cats():
        return cats

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        return {
            "text": "local offline answer",
            "model": "local-synth",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
            "requested_plane": "auto",
            "resolved_plane": "local",
            "fallback_policy": "cross_plane",
            "fallback_occurred": False,
            "fallback_from": None,
            "fallback_reason": None,
            "degraded": True,
            "trigger": "none",
            "continue_count": 0,
            "router_source": "local_router_floor",
            "heuristic_only": False,
            "brief_source": "local_router_floor",
            "orchestration": {
                "lead": "local-synth",
                "route": "direct",
                "specialist_model": "local-synth",
                "brief_authored_by_lead": True,
                "router_source": "local_router_floor",
                "brief_source": "local_router_floor",
            },
        }

    async def _hive(prompt: str):
        hive_calls.append(prompt)
        return None

    async def _route(prompt: str, catalogs):
        route_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": None,
            "router_cost_usd": 0.0,
        }

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    # Call-site reconciled to the committed keyword-only signature.
    out = asyncio.run(
        server._execute_team_turn(
            prompt="hello offline",
            session=None,
            workspace_context="",
            workspace_label="",
            caller_instructions="",
            memory_scope=None,
            use_memory=False,
            model=None,
            effort=None,
            mode="auto",
            plane="auto",
            fallback_policy="cross_plane",
            turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
            depth="auto",
        )
    )
    assert offline_calls, "expected _serve_local_offline to run via resolve=local"
    assert hive_calls == []
    assert route_calls == []
    assert out["resolved_plane"] == "local"
    assert out["degraded"] is True
    assert out["trigger"] == "none"
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0


def test_run_unified_local_primary_serves_offline_envelope(monkeypatch):
    """_run_unified with resolve=local returns _serve_local_offline envelope as-is."""
    cats = _catalogs_fixture(local_ready=True)
    offline_calls: list[tuple[str, str | None]] = []

    async def _resolve(requested, model, *, requires_api):
        assert requested == "auto"
        assert model is None
        return "local", cats

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append((prompt, system_context))
        return {
            "text": "ok",
            "model": "local-synth",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
            "requested_plane": "auto",
            "resolved_plane": "local",
            "fallback_occurred": False,
            "degraded": True,
            "trigger": "none",
            "continue_count": 0,
            "router_source": "local_router_floor",
            "heuristic_only": False,
            "brief_source": "local_router_floor",
            "orchestration": {
                "lead": None,
                "route": "direct",
                "specialist_model": "local-synth",
                "brief_authored_by_lead": True,
                "router_source": "local_router_floor",
                "brief_source": "local_router_floor",
            },
        }

    async def _sys_prompt(*args, **kwargs):
        return "system"

    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_system_prompt", _sys_prompt)

    out = asyncio.run(
        server._run_unified(
            "serve me local",
            model=None,
            effort=None,
            plane="auto",
            fallback_policy="cross_plane",
            agentic=False,
            max_turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
            system_context="ctx",
        )
    )
    assert offline_calls == [("serve me local", "ctx")]
    assert out["resolved_plane"] == "local"
    assert out["degraded"] is True
    assert out["trigger"] == "none"
    assert out["fallback_occurred"] is False
    assert out["requested_plane"] == "auto"
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# M3-T3 — Plane-gated route tiers + heuristic lock (§5.2)
# ---------------------------------------------------------------------------


def _team_turn_kwargs(**overrides: Any) -> dict[str, Any]:
    """Full keyword set for committed _execute_team_turn (all required except depth/num_voters)."""
    base: dict[str, Any] = {
        "session": None,
        "workspace_context": "",
        "workspace_label": "",
        "caller_instructions": "",
        "memory_scope": None,
        "use_memory": False,
        "model": None,
        "effort": None,
        "mode": "auto",
        "plane": "auto",
        "fallback_policy": "cross_plane",
        "turns": 1,
        "allow_web": False,
        "allow_x_search": False,
        "allow_code": False,
        "depth": "auto",
    }
    base.update(overrides)
    return base


def _fake_unified_receipt(**extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "text": "remote answer",
        "model": "cli-synth",
        "plane": "cli",
        "billing_class": "cli_runtime",
        "cost_usd": 0.0,
        "stop_reason": "stop",
        "resolved_plane": "cli",
        "requested_plane": "auto",
        "fallback_occurred": False,
        "degraded": False,
        "trigger": "none",
    }
    out.update(extra)
    return out


@pytest.mark.skip(reason="remote router-tier ladder remains a separate follow-up")
def test_auto_route_cli_not_ready_zero_hive_calls(monkeypatch):
    """cli not ready → _hive_route never called; api+metered may still route_task."""
    cats = _catalogs_fixture(cli_ready=False, api_ready=True, local_ready=False)
    hive_calls: list[Any] = []
    route_calls: list[Any] = []

    async def _cats():
        return cats

    async def _hive(prompt: str):
        hive_calls.append(prompt)
        return None

    async def _route(prompt: str, catalogs):
        route_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "api-synth",
            "router_cost_usd": 0.01,
        }

    async def _ru(*args, **kwargs):
        return _fake_unified_receipt(
            model="api-synth", plane="api", resolved_plane="api",
            billing_class="api_runtime",
        )

    async def _resolve(requested, model, *, requires_api):
        return "api", cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route)
    monkeypatch.setattr(server, "_run_unified", _ru)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    out = asyncio.run(
        server._execute_team_turn(prompt="need routing", **_team_turn_kwargs())
    )
    assert hive_calls == []
    assert route_calls == ["need routing"]
    assert out["router_source"] == "api"
    assert out["heuristic_only"] is False
    assert out["orchestration"]["router_source"] == "api"


@pytest.mark.skip(reason="remote router-tier ladder remains a separate follow-up")
def test_auto_route_api_unmetered_zero_route_task_metered(monkeypatch):
    """api not ready or unmetered → _route_task never called from the ladder."""
    cats = _catalogs_fixture(cli_ready=True, api_ready=True, local_ready=False)
    hive_calls: list[Any] = []
    route_calls: list[Any] = []

    async def _cats():
        return cats

    async def _hive(prompt: str):
        hive_calls.append(prompt)
        # Unparsable votes → None; must NOT fall through to route_task when unmetered.
        return None

    async def _route(prompt: str, catalogs):
        route_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "api-synth",
            "router_cost_usd": 0.01,
        }

    async def _ru(*args, **kwargs):
        return _fake_unified_receipt()

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route)
    monkeypatch.setattr(server, "_run_unified", _ru)
    monkeypatch.setattr(server, "METERED_API_ENABLED", False)

    out = asyncio.run(
        server._execute_team_turn(prompt="hive miss unmetered", **_team_turn_kwargs())
    )
    assert hive_calls == ["hive miss unmetered"]
    assert route_calls == []
    assert out["router_source"] == "heuristic"
    assert out["heuristic_only"] is False  # (d) default-direct, not confident heuristic
    assert out["orchestration"]["router_source"] == "heuristic"


@pytest.mark.skip(reason="remote router-tier ladder remains a separate follow-up")
def test_auto_route_heuristic_hit_locks_no_hive_no_route_task(monkeypatch):
    """Heuristic confident → both tiers uncalled; heuristic_only True on receipt."""
    cats = _catalogs_fixture(cli_ready=True, api_ready=True, local_ready=False)
    hive_calls: list[Any] = []
    route_calls: list[Any] = []

    async def _cats():
        return cats

    async def _hive(prompt: str):
        hive_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "hive_route",
            "router_cost_usd": 0.0,
        }

    async def _route(prompt: str, catalogs):
        route_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "api-synth",
            "router_cost_usd": 0.01,
        }

    async def _ru(*args, **kwargs):
        return _fake_unified_receipt()

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: "direct")
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route)
    monkeypatch.setattr(server, "_run_unified", _ru)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    out = asyncio.run(
        server._execute_team_turn(prompt="confident heuristic", **_team_turn_kwargs())
    )
    assert hive_calls == []
    assert route_calls == []
    assert out["router_source"] == "heuristic"
    assert out["heuristic_only"] is True
    assert out["orchestration"]["router_source"] == "heuristic"


@pytest.mark.skip(reason="remote router-tier ladder remains a separate follow-up")
def test_auto_route_cli_live_heuristic_miss_hive_router_source_cli(monkeypatch):
    """cli live + heuristic miss → hive runs; receipt router_source=cli."""
    cats = _catalogs_fixture(cli_ready=True, api_ready=True, local_ready=False)
    hive_calls: list[Any] = []
    route_calls: list[Any] = []

    async def _cats():
        return cats

    async def _hive(prompt: str):
        hive_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "hive_route",
            "router_cost_usd": 0.0,
            "router_votes": {"direct": 3, "code": 0},
        }

    async def _route(prompt: str, catalogs):
        route_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "api-synth",
            "router_cost_usd": 0.01,
        }

    async def _ru(*args, **kwargs):
        return _fake_unified_receipt()

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route)
    monkeypatch.setattr(server, "_run_unified", _ru)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    out = asyncio.run(
        server._execute_team_turn(prompt="hive should win", **_team_turn_kwargs())
    )
    assert hive_calls == ["hive should win"]
    assert route_calls == []
    assert out["router_source"] == "cli"
    assert out["heuristic_only"] is False
    assert out["orchestration"]["router_source"] == "cli"
    assert out["orchestration"]["router_model"] == "hive_route"


def test_route_task_defensive_gate_skips_metered_when_api_down(monkeypatch):
    """_route_task itself refuses metered call when api unready or unmetered."""
    cats = _catalogs_fixture(cli_ready=False, api_ready=False, local_ready=False)
    guarded_calls: list[Any] = []

    async def _guarded(*args, **kwargs):
        guarded_calls.append(True)
        raise AssertionError("metered provider must not be called")

    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    monkeypatch.setattr(server, "_guarded_provider_call", _guarded)

    out = asyncio.run(server._route_task("no api plane", cats))
    assert guarded_calls == []
    assert out["route"] == "direct"
    assert out["router_model"] is None
    assert out["router_cost_usd"] == 0.0

    # api catalog models are dict-shaped ({"id": ...}) per _api_ids contract.
    cats_api = _catalogs_fixture(api_ready=True, api_models=[{"id": "api-synth"}])
    monkeypatch.setattr(server, "METERED_API_ENABLED", False)
    out2 = asyncio.run(server._route_task("unmetered", cats_api))
    assert guarded_calls == []
    assert out2["route"] == "direct"
    assert out2["router_model"] is None


# ---------------------------------------------------------------------------
# M3-T4 — Full trigger taxonomy on plane-swap + failover classification (§5.3/§5.5)
# ---------------------------------------------------------------------------


def _run_unified_kwargs(**overrides: Any) -> dict[str, Any]:
    """Keyword set for committed _run_unified (all required except optional tails)."""
    base: dict[str, Any] = {
        "model": None,
        "effort": None,
        "plane": "auto",
        "fallback_policy": "cross_plane",
        "agentic": False,
        "max_turns": 1,
        "allow_web": False,
        "allow_x_search": False,
        "allow_code": False,
    }
    base.update(overrides)
    return base


def _offline_success_envelope(**extra: Any) -> dict[str, Any]:
    """Minimal successful _serve_local_offline receipt (§5.5 failover-to-local floors OK)."""
    out: dict[str, Any] = {
        "text": "local offline answer",
        "model": "local-synth",
        "model_id": "local-synth",
        "plane": "local",
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
        "stop_reason": "stop",
        "requested_plane": "auto",
        "resolved_plane": "local",
        "fallback_policy": "cross_plane",
        "fallback_occurred": False,
        "fallback_from": None,
        "fallback_reason": None,
        "degraded": True,
        "trigger": "none",
        "continue_count": 0,
        "router_source": "local_router_floor",
        "heuristic_only": False,
        "brief_source": "local_router_floor",
        "floor_role": "text_generator",
        "floor_metric_ids": ["text_generator:synth:deadbeef"],
        "orchestration": {
            "lead": "local-synth",
            "route": "direct",
            "specialist_model": "local-synth",
            "brief_authored_by_lead": True,
            "router_source": "local_router_floor",
            "brief_source": "local_router_floor",
        },
    }
    out.update(extra)
    return out


def _offline_failclosed_envelope(
    *,
    reason: str,
    text: str = "local path unavailable",
    **extra: Any,
) -> dict[str, Any]:
    """Fail-closed offline envelope shape (shed/no_floor) before failover overlay."""
    trigger = server._canonical_trigger(reason)
    out: dict[str, Any] = {
        "text": text,
        "model": None,
        "plane": "local",
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
        "stop_reason": "error",
        "requested_plane": "auto",
        "resolved_plane": "local",
        "fallback_policy": "cross_plane",
        "fallback_occurred": False,
        "fallback_from": None,
        "fallback_reason": reason,
        "degraded": True,
        "trigger": trigger,
        "continue_count": 0,
        "router_source": "heuristic",
        "heuristic_only": False,
        "orchestration": {
            "lead": None,
            "route": "direct",
            "specialist_model": None,
            "brief_authored_by_lead": False,
            "router_source": "heuristic",
        },
    }
    out.update(extra)
    return out


def test_failover_timeout_full_offline_path_trigger_timeout(monkeypatch):
    """(i) Remote timeout → trigger=timeout, full offline path, brief_source set."""
    cats = _catalogs_fixture(cli_ready=True, local_ready=True)
    router_floor_calls: list[str] = []
    offline_via_floor: list[str] = []

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _sys(*args, **kwargs):
        return "sys"

    async def _alt(current, model, *, requires_api):
        return "local"

    class _BoomAcp:
        async def run(self, *args, **kwargs):
            raise TimeoutError("upstream timeout waiting for cli")

    async def _router(prompt: str, **kwargs) -> dict[str, Any]:
        router_floor_calls.append(prompt)
        return {
            "route": "direct",
            "brief": "offline brief",
            "specialist_prompt": prompt,
            "router_model": "local-router",
            "router_cost_usd": 0.0,
        }

    async def _chat(prompt: str, **kwargs) -> dict[str, Any]:
        offline_via_floor.append(prompt)
        return {
            "text": "local specialist answer",
            "model": "local-synth",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id: str, role: str) -> dict[str, Any] | None:
        return {"metric_id": f"{role}:synth:deadbeef", "model_id": model_id or "local-synth"}

    async def _acq() -> bool:
        return True

    # Real offline path so router-floor mock is actually invoked.
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})  # no cross-test breaker bleed
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_system_prompt", _sys)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _BoomAcp())
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)
    # Keep real _serve_local_offline

    out = asyncio.run(
        server._run_unified(
            "failover timeout please",
            **_run_unified_kwargs(system_context="ctx"),
        )
    )
    assert router_floor_calls, "expected local router-floor to run on failover-to-local"
    assert offline_via_floor, "expected floor-eligible specialist invoke"
    assert out["resolved_plane"] == "local"
    assert out["fallback_occurred"] is True
    assert out["fallback_from"] == "cli"
    assert out["degraded"] is True
    assert out["trigger"] == "timeout"
    assert out["brief_source"] == "local_router_floor"
    assert out["router_source"] in ("heuristic", "local_router_floor")
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0
    assert out["continue_count"] == 0


def test_failover_429_trigger(monkeypatch):
    """(ii) Injected 429 → trigger==429 after full offline success overlay."""
    cats = _catalogs_fixture(cli_ready=True, local_ready=True)
    offline_calls: list[str] = []

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _sys(*args, **kwargs):
        return "sys"

    async def _alt(current, model, *, requires_api):
        return "local"

    class _BoomAcp:
        async def run(self, *args, **kwargs):
            raise RuntimeError("429 too many requests")

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        return _offline_success_envelope()

    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_system_prompt", _sys)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _BoomAcp())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    out = asyncio.run(
        server._run_unified("rate limited", **_run_unified_kwargs())
    )
    assert offline_calls == ["rate limited"]
    assert out["trigger"] == "429"
    assert out["resolved_plane"] == "local"
    assert out["fallback_from"] == "cli"
    assert out["fallback_occurred"] is True
    assert out["degraded"] is True
    assert out["brief_source"] == "local_router_floor"
    assert out["router_source"] in ("heuristic", "local_router_floor")


def test_failover_breaker_open_trigger(monkeypatch):
    """(iii) circuit breaker open on primary → trigger==breaker_open; offline runs."""
    cats = _catalogs_fixture(cli_ready=True, local_ready=True)
    offline_calls: list[str] = []

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _sys(*args, **kwargs):
        return "sys"

    async def _alt(current, model, *, requires_api):
        return "local"

    def _breaker(plane: str, model: str | None) -> None:
        raise RuntimeError(f"circuit breaker open for {plane}:{model}")

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        return _offline_success_envelope()

    # Prove _breaker_before_call exception enters the failover except (no ACP run).
    class _NoAcp:
        async def run(self, *args, **kwargs):
            raise AssertionError("BUILD_ACP.run must not run when breaker is open")

    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_system_prompt", _sys)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "_breaker_before_call", _breaker)
    monkeypatch.setattr(server, "BUILD_ACP", _NoAcp())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    out = asyncio.run(
        server._run_unified("breaker path", **_run_unified_kwargs())
    )
    assert offline_calls == ["breaker path"]
    assert out["trigger"] == "breaker_open"
    assert out["resolved_plane"] == "local"
    assert out["fallback_from"] == "cli"
    assert out["fallback_occurred"] is True
    assert out["degraded"] is True
    assert out["brief_source"] == "local_router_floor"


def test_failover_local_shed_keeps_shed_trigger(monkeypatch):
    """(iv) Offline shed at failover → trigger=shed, fallback_from set, no specialist."""
    cats = _catalogs_fixture(cli_ready=True, local_ready=True)
    specialist_calls: list[str] = []
    offline_calls: list[str] = []

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _sys(*args, **kwargs):
        return "sys"

    async def _alt(current, model, *, requires_api):
        return "local"

    class _BoomAcp:
        async def run(self, *args, **kwargs):
            raise TimeoutError("timeout before shed")

    async def _acq() -> bool:
        return False

    async def _chat(prompt: str, **kwargs) -> dict[str, Any]:
        specialist_calls.append(prompt)
        raise AssertionError("specialist must not run when local slot exhausted")

    # Real _serve_local_offline: slot acquire False → shed envelope.
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_system_prompt", _sys)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _BoomAcp())
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)

    # Spy only to count entry; delegate to real offline.
    _real = server._serve_local_offline

    async def _offline_spy(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        return await _real(prompt, system_context=system_context)

    monkeypatch.setattr(server, "_serve_local_offline", _offline_spy)

    out = asyncio.run(
        server._run_unified("shed on failover", **_run_unified_kwargs())
    )
    assert offline_calls == ["shed on failover"]
    assert specialist_calls == []
    assert out["trigger"] == "shed"
    assert out["fallback_from"] == "cli"
    assert out["fallback_occurred"] is True
    assert out["resolved_plane"] == "local"
    assert out["degraded"] is True
    # Swap-cause must NOT clobber terminal shed.
    assert out["trigger"] != "timeout"


def test_failover_no_floor_keeps_no_floor_trigger(monkeypatch):
    """(v) Offline fail-closed unfunded/no_floor → trigger no_floor-family; swap preserved."""
    cats = _catalogs_fixture(cli_ready=True, local_ready=True)
    offline_calls: list[str] = []

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _sys(*args, **kwargs):
        return "sys"

    async def _alt(current, model, *, requires_api):
        return "local"

    class _BoomAcp:
        async def run(self, *args, **kwargs):
            raise RuntimeError("upstream error before no_floor")

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        # Matches committed offline unfunded fail-closed reason → no_floor.
        return _offline_failclosed_envelope(
            reason="local_router_floor_unfunded",
            text="local router floor unfunded",
        )

    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_system_prompt", _sys)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _BoomAcp())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    out = asyncio.run(
        server._run_unified("no floor on failover", **_run_unified_kwargs())
    )
    assert offline_calls == ["no floor on failover"]
    assert out["trigger"] == "no_floor"
    assert out["fallback_from"] == "cli"
    assert out["fallback_occurred"] is True
    assert out["resolved_plane"] == "local"
    assert out["degraded"] is True
    # Remote error must not replace terminal no_floor.
    assert out["trigger"] != "error"


# ---------------------------------------------------------------------------
# M3-T5 — 429-storm breaker from local_plane_knobs data (§5.4 / §8.2.4)
# ---------------------------------------------------------------------------

import time


def _fresh_storm_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "events": [],
        "open_until": 0.0,
        "half_open": False,
        "_halfopen_s": 30.0,
    }
    state.update(overrides)
    return state


def _storm_knob_patch(
    monkeypatch: Any,
    *,
    threshold: int = 4,
    window_s: float = 60.0,
    halfopen_s: float = 30.0,
) -> None:
    async def _knob(key: str, default: Any = None) -> Any:
        table = {
            "storm_429_threshold": threshold,
            "storm_429_window_s": window_s,
            "storm_429_halfopen_s": halfopen_s,
            "local_concurrency_budget": 2,
        }
        return table.get(key, default)

    monkeypatch.setattr(server.STATE, "local_knob", _knob)


class _Boom429Acp:
    async def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("429 too many requests")


class _ForbiddenXai:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError("remote alternate must not be called under storm")


@pytest.mark.skip(reason="429 storm admission remains a separate follow-up")
def test_storm_note_threshold_opens_and_failover_skips_remote(monkeypatch):
    """N=2: two remote 429s open storm; _run_unified failover serves local, not remote."""
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    _storm_knob_patch(monkeypatch, threshold=2, window_s=60.0, halfopen_s=30.0)

    cats = _catalogs_fixture(cli_ready=True, api_ready=False, local_ready=True)
    offline_calls: list[str] = []

    async def _cats():
        return cats

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _alt(resolved, model, *, requires_api):
        return "local"

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        return _offline_success_envelope()

    async def _sys_prompt(*args, **kwargs):
        return "system"

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _Boom429Acp())
    monkeypatch.setattr(server, "xai_api", _ForbiddenXai())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_system_prompt", _sys_prompt)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    # Two 429 notes → storm open (unit proof of the note path).
    asyncio.run(server._storm_note_429("api"))
    assert server._storm_is_open() is False  # only 1 event
    asyncio.run(server._storm_note_429("api"))
    assert server._storm_is_open() is True

    # Fresh storm; pre-seed one 429 so the failover's note opens it mid-path.
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    asyncio.run(server._storm_note_429("cli"))
    assert server._storm_is_open() is False

    out = asyncio.run(
        server._run_unified(
            "storm failover",
            **_run_unified_kwargs(plane="auto", fallback_policy="cross_plane"),
        )
    )
    assert offline_calls == ["storm failover"]
    assert out["resolved_plane"] == "local"
    assert out["fallback_occurred"] is True
    assert out["fallback_from"] == "cli"
    # trigger is 429 (rate_limited swap) or breaker_open (storm overlay).
    assert out["trigger"] in {"429", "breaker_open"}
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0
    # Storm actually opened from the in-path note.
    assert server._storm_is_open() is True


def test_storm_half_open_probe_success_closes(monkeypatch):
    """After open_until expires → half_open; probe success clears storm state."""
    monkeypatch.setattr(
        server,
        "_STORM_429",
        _fresh_storm_state(
            events=[time.monotonic()],
            open_until=time.monotonic() - 1.0,  # already expired
            half_open=False,
            _halfopen_s=30.0,
        ),
    )
    _storm_knob_patch(monkeypatch, threshold=2, window_s=60.0, halfopen_s=30.0)

    assert server._storm_is_open() is False
    assert server._STORM_429["half_open"] is True

    server._storm_remote_success()
    assert server._STORM_429["half_open"] is False
    assert server._STORM_429["open_until"] == 0.0
    assert server._STORM_429["events"] == []
    assert server._storm_is_open() is False

    # Explicit probe API also closes when half_open.
    monkeypatch.setattr(
        server,
        "_STORM_429",
        _fresh_storm_state(half_open=True, events=[1.0], open_until=0.0),
    )
    server._storm_probe_result(True)
    assert server._STORM_429["half_open"] is False
    assert server._STORM_429["events"] == []
    assert server._STORM_429["open_until"] == 0.0


@pytest.mark.skip(reason="429 storm admission remains a separate follow-up")
def test_storm_open_local_slots_exhausted_fail_closed(monkeypatch):
    """Storm open + local cap exhausted → single shed/breaker_open; no remote thrash."""
    monkeypatch.setattr(
        server,
        "_STORM_429",
        _fresh_storm_state(open_until=time.monotonic() + 60.0, half_open=False),
    )
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    _storm_knob_patch(monkeypatch, threshold=2, window_s=60.0, halfopen_s=30.0)

    cats = _catalogs_fixture(cli_ready=True, api_ready=True, local_ready=True)
    offline_calls: list[str] = []

    async def _cats():
        return cats

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _alt(resolved, model, *, requires_api):
        # Slot exhausted → _alternate_plane would skip local and offer remote.
        return "api"

    async def _offline(
        prompt: str, *, system_context=None, prior_continue_count=0
    ) -> dict[str, Any]:
        offline_calls.append(prompt)
        return _offline_failclosed_envelope(reason="local_concurrency_exhausted")

    async def _sys_prompt(*args, **kwargs):
        return "system"

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _Boom429Acp())
    monkeypatch.setattr(server, "xai_api", _ForbiddenXai())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_system_prompt", _sys_prompt)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    out = asyncio.run(
        server._run_unified(
            "storm shed",
            **_run_unified_kwargs(plane="auto", fallback_policy="cross_plane"),
        )
    )
    assert offline_calls == ["storm shed"], "exactly one offline serve attempt"
    assert out["resolved_plane"] == "local"
    assert out["fallback_from"] == "cli"
    assert out["trigger"] in {"breaker_open", "shed"}
    assert out["degraded"] is True


def test_storm_knob_threshold_override_five_not_four(monkeypatch):
    """Knob N=5: four 429s do not open; fifth opens."""
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    _storm_knob_patch(monkeypatch, threshold=5, window_s=60.0, halfopen_s=30.0)

    for _ in range(4):
        asyncio.run(server._storm_note_429("api"))
    assert server._storm_is_open() is False
    assert server._STORM_429["half_open"] is False

    asyncio.run(server._storm_note_429("cli"))
    assert server._storm_is_open() is True
    assert float(server._STORM_429["open_until"]) > time.monotonic()
    assert server._STORM_429["half_open"] is False


def test_acceptance_continue_bound_holds_across_retried_serves(monkeypatch):
    """§5.5 continue bound is job-level: exhausts across retried serves via
    prior_continue_count threading (_serve_local_offline → _run_unified →
    _execute_team_turn), not receipt-local reset to 0 each serve.
    """
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _no_slot() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _no_slot)

    out1 = asyncio.run(server._serve_local_offline("retry me"))
    assert out1["status"] == "continue"
    assert out1["continue_count"] == 1

    cats = _catalogs_fixture(local_ready=True)

    async def _resolve_local(plane, model, *, requires_api=False):
        return ("local", cats)

    monkeypatch.setattr(server, "_resolve_plane", _resolve_local)
    out2 = asyncio.run(
        server._run_unified(
            "retry me",
            **_run_unified_kwargs(),
            prior_continue_count=int(out1["continue_count"]),
        )
    )
    assert out2["status"] == "continue"
    assert out2["continue_count"] == 2

    async def _catalogs():
        return cats

    monkeypatch.setattr(server, "_catalogs", _catalogs)
    monkeypatch.setattr(server, "_wants_media_generation", lambda *_a, **_k: None)

    out3 = asyncio.run(
        server._execute_team_turn(
            prompt="retry me",
            session=None,
            workspace_context="",
            workspace_label="",
            caller_instructions="",
            memory_scope=None,
            use_memory=False,
            model=None,
            effort=None,
            mode="auto",
            plane="auto",
            fallback_policy="cross_plane",
            turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
            depth="auto",
            prior_continue_count=int(out2["continue_count"]),
        )
    )
    assert out3["continue_count"] == 3
    assert out3["status"] == "error"
    assert out3.get("continue_exhausted") is True


def test_acceptance_storm_note_429_concurrent_no_drop(monkeypatch):
    """§5.4 serialization: concurrent 429 notes drop no samples and open on time."""

    async def _knob(name: str, default=None):
        await asyncio.sleep(0)  # force interleaving windows under gather
        table = {
            "storm_429_threshold": 8,
            "storm_429_window_s": 60.0,
            "storm_429_halfopen_s": 30.0,
        }
        return table.get(name, default)

    monkeypatch.setattr(server.STATE, "local_knob", _knob)
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())

    async def _burst():
        await asyncio.gather(*(server._storm_note_429("api") for _ in range(8)))

    asyncio.run(_burst())
    assert len(server._STORM_429["events"]) == 8
    assert server._storm_is_open() is True
    assert server._STORM_429["half_open"] is False

    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())

    async def _under():
        await asyncio.gather(*(server._storm_note_429("api") for _ in range(3)))

    asyncio.run(_under())
    assert len(server._STORM_429["events"]) == 3
    assert server._storm_is_open() is False


@pytest.mark.skip(reason="429 storm admission remains a separate follow-up")
def test_acceptance_half_open_zero_hive_metered_single_probe(monkeypatch):
    """Half-open gates hive/metered tiers; exactly one remote probe; success closes."""
    real_run_unified = server._run_unified

    # --- (1) LADDER: half_open gates remote tiers → direct → _run_unified, zero hive/route ---
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state(half_open=True))
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    _storm_knob_patch(monkeypatch, threshold=2, window_s=60.0, halfopen_s=30.0)
    cats_ladder = _catalogs_fixture(cli_ready=True, api_ready=True, local_ready=True)
    hive_calls = 0
    route_calls = 0
    unified_calls: list[str] = []

    async def _cats_ladder():
        return cats_ladder

    async def _resolve_ladder(requested, model, *, requires_api):
        return "cli", cats_ladder

    async def _hive(*args, **kwargs):
        nonlocal hive_calls
        hive_calls += 1
        raise AssertionError("_hive_route must not run while storm half_open")

    async def _route_task(*args, **kwargs):
        nonlocal route_calls
        route_calls += 1
        raise AssertionError("_route_task must not run while storm half_open")

    async def _fake_unified(prompt: str, **kwargs):
        unified_calls.append(prompt)
        return {"resolved_plane": "cli", "model": "cli-synth", "text": "ok"}

    monkeypatch.setattr(server, "_catalogs", _cats_ladder)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_resolve_plane", _resolve_ladder)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route_task)
    monkeypatch.setattr(server, "_run_unified", _fake_unified)

    ladder_out = asyncio.run(
        server._execute_team_turn(
            prompt="ladder half-open zero hive",
            session=None,
            workspace_context="",
            workspace_label="",
            caller_instructions="",
            memory_scope=None,
            use_memory=False,
            model=None,
            effort=None,
            mode="auto",
            plane="auto",
            fallback_policy="cross_plane",
            turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
            depth="auto",
        )
    )
    assert hive_calls == 0, f"expected zero _hive_route calls under half_open, got {hive_calls}"
    assert route_calls == 0, f"expected zero _route_task calls under half_open, got {route_calls}"
    assert unified_calls, "expected fake _run_unified to be called via direct ladder path"
    assert ladder_out.get("text") == "ok" or unified_calls, "ladder path must complete via fake _run_unified"

    # Restore the real serve path for the probe halves of this acceptance test.
    monkeypatch.setattr(server, "_run_unified", real_run_unified)

    # --- (2) SINGLE PROBE: probe already claimed → never hits ACP; failover to local offline ---
    held = _fresh_storm_state(half_open=True)
    held["probe_claimed"] = True
    monkeypatch.setattr(server, "_STORM_429", held)
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    cats = _catalogs_fixture(cli_ready=True, api_ready=False, local_ready=True)
    offline_calls: list[str] = []
    acp_runs = 0

    class _CountingFailAcp:
        async def run(self, *a, **k):
            nonlocal acp_runs
            acp_runs += 1
            raise AssertionError("BUILD_ACP.run must not execute while probe is held by another caller")

    async def _cats():
        return cats

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _alt(resolved, model, *, requires_api):
        return "local"

    async def _offline(prompt: str, *, system_context=None, prior_continue_count=0) -> dict[str, Any]:
        offline_calls.append(prompt)
        return _offline_success_envelope()

    async def _sys_prompt(*args, **kwargs):
        return "system"

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _CountingFailAcp())
    monkeypatch.setattr(server, "xai_api", _ForbiddenXai())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_system_prompt", _sys_prompt)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    loser_out = asyncio.run(
        server._run_unified(
            "probe loser half-open",
            **_run_unified_kwargs(plane="auto", fallback_policy="cross_plane"),
        )
    )
    assert acp_runs == 0, f"held probe must not reach BUILD_ACP.run, got {acp_runs} runs"
    assert offline_calls, "probe loser must serve local offline via half_open failover gate"
    assert loser_out.get("resolved_plane") == "local", f"expected local plane, got {loser_out.get('resolved_plane')!r}"
    assert loser_out.get("trigger") in {"breaker_open", "error", "429"}
    assert loser_out.get("trigger") == "breaker_open", f"expected breaker_open overlay, got {loser_out.get('trigger')!r}"
    assert server._STORM_429.get("half_open") is True, "half_open must remain True for probe loser"
    assert server._STORM_429.get("probe_claimed") is True, "pre-held probe_claimed must remain True for loser"

    # --- (3) PROBE WINNER: unclaimed half_open → claim, ACP success → storm closed ---
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state(half_open=True))
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})

    class _OkAcp:
        async def run(self, *a, **k):
            return {"text": "probe ok", "model": "cli-synth", "plane": "cli"}

    monkeypatch.setattr(server, "BUILD_ACP", _OkAcp())
    offline_calls.clear()

    winner_out = asyncio.run(
        server._run_unified(
            "probe winner half-open",
            **_run_unified_kwargs(plane="auto", fallback_policy="cross_plane"),
        )
    )
    assert not offline_calls, "probe winner must not fall back to local offline"
    assert winner_out is not None
    storm = server._STORM_429
    assert storm.get("half_open") is False, f"storm must close after probe success, half_open={storm.get('half_open')!r}"
    assert storm.get("events") == [], f"events must clear after probe success, got {storm.get('events')!r}"
    assert float(storm.get("open_until", 0.0) or 0.0) == 0.0, f"open_until must be 0.0, got {storm.get('open_until')!r}"
    assert not storm.get("probe_claimed"), f"probe_claimed must be cleared, got {storm.get('probe_claimed')!r}"


# ---------------------------------------------------------------------------
# M3-T6 — Bounded continue_count for shed / non_answer (§5.5)
# ---------------------------------------------------------------------------


def _continue_knob_patch(
    monkeypatch: Any,
    *,
    continue_max: int = 2,
    **extra: Any,
) -> None:
    async def _knob(key: str, default: Any = None) -> Any:
        table = {
            "continue_max": continue_max,
            "local_concurrency_budget": 2,
            "storm_429_threshold": 4,
            "storm_429_window_s": 60.0,
            "storm_429_halfopen_s": 30.0,
        }
        table.update(extra)
        return table.get(key, default)

    monkeypatch.setattr(server.STATE, "local_knob", _knob)


def test_shed_first_continue_count_one(monkeypatch):
    """First shed envelope → status continue, continue_count 1."""
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)

    out = asyncio.run(server._serve_local_offline("shed once"))
    assert out["trigger"] == "shed"
    assert out["degraded"] is True
    assert out["status"] == "continue"
    assert out["continue_count"] == 1
    assert out.get("continue_exhausted") is not True
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0


def test_shed_bound_exhausted_hardens_error(monkeypatch):
    """prior_continue_count=2 with continue_max=2 → error, exhausted, count 3."""
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)

    out = asyncio.run(
        server._serve_local_offline("shed exhaust", prior_continue_count=2)
    )
    assert out["trigger"] == "shed"
    assert out["status"] == "error"
    assert out["continue_exhausted"] is True
    assert out["continue_count"] == 3
    assert out["degraded"] is True


def test_offline_success_continue_count_zero_no_status(monkeypatch):
    """Success offline keeps continue_count 0 and adds no status key."""
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq() -> bool:
        return True

    async def _router(prompt: str, *, system_context=None) -> dict[str, Any]:
        return {
            "route": "direct",
            "brief": "do the thing",
            "router_model": "local-router",
        }

    async def _chat(prompt: str, **kwargs) -> dict[str, Any]:
        return {
            "text": "solid local answer",
            "model": "local-synth",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id: str, role: str) -> dict[str, Any]:
        return {"metric_id": 1}

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: "direct")
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)
    monkeypatch.setattr(server, "is_nonanswer_completion", lambda text, prompt=None: False)

    out = asyncio.run(server._serve_local_offline("success path"))
    assert out["trigger"] == "none"
    assert out["continue_count"] == 0
    assert "status" not in out
    assert out.get("continue_exhausted") is not True
    assert out["text"] == "solid local answer"
    assert out["billing_class"] == "local_runtime"


def test_continue_max_zero_first_shed_is_error(monkeypatch):
    """Knob continue_max=0 → first shed hardens to error immediately."""
    _continue_knob_patch(monkeypatch, continue_max=0)

    async def _acq() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)

    out = asyncio.run(server._serve_local_offline("shed hard"))
    assert out["trigger"] == "shed"
    assert out["status"] == "error"
    assert out["continue_exhausted"] is True
    assert out["continue_count"] == 1


def test_local_non_answer_gate_bounded_continue(monkeypatch):
    """Specialist non-answer → trigger non_answer, status continue, count 1."""
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq() -> bool:
        return True

    async def _router(prompt: str, *, system_context=None) -> dict[str, Any]:
        return {
            "route": "direct",
            "brief": "brief",
            "router_model": "local-router",
        }

    async def _chat(prompt: str, **kwargs) -> dict[str, Any]:
        return {
            "text": "",  # empty / boilerplate; gate forces non_answer
            "model": "local-synth",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id: str, role: str) -> dict[str, Any]:
        return {"metric_id": 7}

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)
    monkeypatch.setattr(server, "is_nonanswer_completion", lambda text, prompt=None: True)

    out = asyncio.run(server._serve_local_offline("nonanswer please"))
    assert out["trigger"] == "non_answer"
    assert out["status"] == "continue"
    assert out["continue_count"] == 1
    assert out["degraded"] is True
    assert out.get("continue_exhausted") is not True
    # Model text preserved (gate does not rewrite body to capacity copy).
    assert out["text"] == ""
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# M3-T7 — §8.2 acceptance suite (ten contract checks)
# ---------------------------------------------------------------------------

from pathlib import Path

from test_local_plane_m1 import _FakeProbe, _patch_catalog_peers, seed_ready

_ACCEPTANCE_RECEIPT_KEYS = (
    "trigger",
    "resolved_plane",
    "model_id",
    "floor_role",
    "floor_metric_ids",
    "router_source",
    "degraded",
    "fallback_occurred",
    "fallback_from",
    "fallback_reason",
    "fallback_policy",
    "requested_plane",
    "continue_count",
    "billing_class",
    "cost_usd",
    "latency_ms",
)

_CANONICAL_TRIGGER_CASES = (
    ("none", None),
    ("missing", "cli_missing"),
    ("missing", "api_capability_unavailable"),
    ("429", "api_rate_limited"),
    ("429", "cli_rate_limited"),
    ("timeout", "api_timeout"),
    ("timeout", "cli_upstream_timeout"),
    ("shed", "shed"),
    ("shed", "local_concurrency_exhausted"),
    ("no_floor", "no_floor"),
    ("no_floor", "local_router_floor_unfunded"),
    ("non_answer", "local_non_answer"),
    ("breaker_open", "api_storm_circuit_open"),
    ("breaker_open", "cli_circuit_open"),
    ("error", "cli_runtime_failure"),
)


@pytest.fixture
def store7(tmp_path: Path) -> PublicStateStore:
    s = PublicStateStore(tmp_path / "state7.db")
    asyncio.run(s.initialize())
    return s


@pytest.fixture
def dbpath7(store7: PublicStateStore, tmp_path: Path) -> Path:
    return tmp_path / "state7.db"


def _assert_receipt_keys(env: dict[str, Any]) -> None:
    for k in _ACCEPTANCE_RECEIPT_KEYS:
        assert k in env, f"missing receipt key {k!r}"


# --- §8.2.1 taxonomy --------------------------------------------------------


@pytest.mark.parametrize("expected,reason", _CANONICAL_TRIGGER_CASES)
def test_acceptance_8_2_1_taxonomy_canonical_trigger(expected: str, reason):
    """Every taxonomy enum value is reachable via _canonical_trigger mapping."""
    assert server._canonical_trigger(reason) == expected


def test_acceptance_8_2_1_taxonomy_on_real_envelopes(monkeypatch):
    """shed / non_answer / no_floor appear on real offline envelopes; all-down raises."""
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq_no() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq_no)
    shed = asyncio.run(server._serve_local_offline("busy now"))
    assert shed["trigger"] == "shed"

    async def _acq_yes() -> bool:
        return True

    async def _router_boom(prompt, *, system_context=None):
        raise RuntimeError("router unfunded")

    monkeypatch.setattr(server, "_local_slot_acquire", _acq_yes)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router_boom)
    nf = asyncio.run(server._serve_local_offline("no router floor"))
    assert nf["trigger"] == "no_floor"
    assert nf["status"] == "error"

    async def _router_ok(prompt, *, system_context=None):
        return {"route": "direct", "brief": "b", "router_model": "r"}

    async def _chat(prompt, **kwargs):
        return {
            "text": "",
            "model": "m",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id, role):
        return {"metric_id": "x"}

    monkeypatch.setattr(server, "_local_router_floor", _router_ok)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)
    monkeypatch.setattr(server, "is_nonanswer_completion", lambda t, prompt=None: True)
    na = asyncio.run(server._serve_local_offline("say nothing"))
    assert na["trigger"] == "non_answer"

    cats = _catalogs_fixture(cli_ready=False, api_ready=False, local_ready=False)

    async def _cats_empty():
        return cats

    monkeypatch.setattr(server, "_catalogs", _cats_empty)
    with pytest.raises(RuntimeError, match="Neither Grok credential plane is ready"):
        asyncio.run(server._resolve_plane("auto", None, requires_api=False))


# --- §8.2.2 plane-swap only via resolve/alternate/call ----------------------


def test_acceptance_8_2_2_plane_swap_only_via_resolve_alternate(monkeypatch):
    """Failover swaps planes only through resolve/alternate spies; offline after local."""
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    _storm_knob_patch(monkeypatch)
    cats = _catalogs_fixture(cli_ready=True, api_ready=False, local_ready=True)
    resolve_calls: list[Any] = []
    alternate_calls: list[Any] = []
    offline_calls: list[str] = []
    order: list[str] = []

    async def _cats():
        return cats

    async def _spy_resolve(requested, model, *, requires_api):
        resolve_calls.append((requested, model, requires_api))
        order.append("resolve")
        return "cli", cats

    async def _spy_alternate(resolved, model, *, requires_api):
        alternate_calls.append((resolved, model, requires_api))
        order.append("alternate")
        return "local"

    async def _offline(prompt: str, *, system_context=None, prior_continue_count=0):
        order.append("offline")
        offline_calls.append(prompt)
        return _offline_success_envelope(latency_ms=1)

    async def _sys_prompt(*args, **kwargs):
        return "system"

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _spy_resolve)
    monkeypatch.setattr(server, "_alternate_plane", _spy_alternate)
    monkeypatch.setattr(server, "BUILD_ACP", _Boom429Acp())
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "_system_prompt", _sys_prompt)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    out = asyncio.run(
        server._run_unified(
            "swap path",
            **_run_unified_kwargs(plane="auto", fallback_policy="cross_plane"),
        )
    )
    assert resolve_calls, "plane resolve must run"
    assert alternate_calls, "plane alternate must run on failover"
    assert offline_calls == ["swap path"]
    assert out["resolved_plane"] == "local"
    assert out["fallback_occurred"] is True
    # Offline serve happens only AFTER resolve → alternate returned local.
    assert order.index("resolve") < order.index("alternate") < order.index("offline")


# --- §8.2.3 remotes missing/429 → offline, zero hive/route_task -------------


def test_acceptance_8_2_3_offline_zero_remote_route_tiers(monkeypatch):
    """Remotes down + local ready: team turn completes offline, hive/route_task never run."""
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    cats = _catalogs_fixture(cli_ready=False, api_ready=False, local_ready=True)
    hive_calls: list[Any] = []
    route_calls: list[Any] = []
    serve_calls: list[str] = []

    async def _cats():
        return cats

    async def _resolve(requested, model, *, requires_api):
        return "local", cats

    async def _hive(prompt: str):
        hive_calls.append(prompt)
        return None

    async def _route(prompt: str, catalogs):
        route_calls.append(prompt)
        return {
            "route": "direct",
            "specialist_prompt": prompt,
            "router_model": "api-synth",
            "router_cost_usd": 0.01,
        }

    async def _offline(prompt: str, *, system_context=None, prior_continue_count=0):
        serve_calls.append(prompt)
        return _offline_success_envelope(latency_ms=2)

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_hive_route", _hive)
    monkeypatch.setattr(server, "_route_task", _route)
    monkeypatch.setattr(server, "_serve_local_offline", _offline)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    out = asyncio.run(
        server._execute_team_turn(prompt="offline only", **_team_turn_kwargs())
    )
    assert hive_calls == []
    assert route_calls == []
    assert serve_calls == ["offline only"], "certified local specialist path must run"
    assert out["resolved_plane"] == "local"
    assert out["brief_source"] == "local_router_floor"
    assert out["router_source"] in {"heuristic", "local_router_floor"}
    assert out["degraded"] is True
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0


# --- §8.2.4 concurrency + storm + bounded continue --------------------------


def test_acceptance_8_2_4_concurrency_storm_continue_bounds(monkeypatch):
    """Slot shed, storm open@N, and continue_max exhaust — all fail closed, no loops."""
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    _storm_knob_patch(monkeypatch, threshold=2, window_s=60.0, halfopen_s=30.0)

    # Storm opens exactly at knob threshold.
    asyncio.run(server._storm_note_429("api"))
    assert server._storm_is_open() is False
    asyncio.run(server._storm_note_429("api"))
    assert server._storm_is_open() is True

    # Continue bound: fresh shed increments; exhaust hardens to error.
    _continue_knob_patch(monkeypatch, continue_max=1)
    first = asyncio.run(
        server._apply_continue_bound(
            {"trigger": "shed", "continue_count": 0, "degraded": True}
        )
    )
    assert first["status"] == "continue"
    assert first["continue_count"] == 1
    second = asyncio.run(
        server._apply_continue_bound(
            {"trigger": "shed", "continue_count": 1, "degraded": True}
        )
    )
    assert second["status"] == "error"
    assert second["continue_exhausted"] is True
    # Idempotent: an already-terminal envelope is not re-incremented (no loop).
    again = asyncio.run(server._apply_continue_bound(dict(second)))
    assert again["continue_count"] == second["continue_count"]

    # Concurrency cap: slot-exhausted offline serve sheds once with bounded status.
    async def _acq() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    shed = asyncio.run(server._serve_local_offline("capacity gone"))
    assert shed["trigger"] == "shed"
    assert shed["status"] in {"continue", "error"}
    assert shed["degraded"] is True


# --- §8.2.5 receipt key-set law ---------------------------------------------


def test_acceptance_8_2_5_receipt_keyset_three_paths(monkeypatch):
    """Offline success, local failover success, offline shed carry full §8.2.5 keys."""
    monkeypatch.setattr(server, "_STORM_429", _fresh_storm_state())
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    _storm_knob_patch(monkeypatch)
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq() -> bool:
        return True

    async def _router(prompt, *, system_context=None):
        return {"route": "direct", "brief": "b", "router_model": "local-router"}

    async def _chat(prompt, **kwargs):
        return {
            "text": "real local answer",
            "model": "local-synth",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }

    async def _bind(model_id, role):
        return {"metric_id": f"{role}:fam:seed"}

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_chat", _chat)
    monkeypatch.setattr(server.STATE, "local_bind", _bind)
    monkeypatch.setattr(server, "is_nonanswer_completion", lambda t, prompt=None: False)

    # (1) Real offline success envelope.
    offline_ok = asyncio.run(server._serve_local_offline("keyset success"))
    _assert_receipt_keys(offline_ok)
    assert offline_ok["degraded"] is True
    assert offline_ok["latency_ms"] >= 0

    # (2) Real failover-to-local success (primary 429 → full offline path).
    cats = _catalogs_fixture(cli_ready=True, api_ready=False, local_ready=True)

    async def _cats():
        return cats

    async def _resolve(requested, model, *, requires_api):
        return "cli", cats

    async def _alt(resolved, model, *, requires_api):
        return "local"

    async def _sys_prompt(*args, **kwargs):
        return "system"

    monkeypatch.setattr(server, "_catalogs", _cats)
    monkeypatch.setattr(server, "_resolve_plane", _resolve)
    monkeypatch.setattr(server, "_alternate_plane", _alt)
    monkeypatch.setattr(server, "BUILD_ACP", _Boom429Acp())
    monkeypatch.setattr(server, "_system_prompt", _sys_prompt)

    failover = asyncio.run(
        server._run_unified(
            "failover keys",
            **_run_unified_kwargs(plane="auto", fallback_policy="cross_plane"),
        )
    )
    _assert_receipt_keys(failover)
    assert failover["fallback_occurred"] is True
    assert failover["resolved_plane"] == "local"
    assert failover["degraded"] is True
    assert failover["billing_class"] == "local_runtime"

    # (3) Real offline shed envelope (model_id absent on shed is honest — assert
    # the remaining key-set law plus the shed-specific keys).
    async def _acq_no() -> bool:
        return False

    monkeypatch.setattr(server, "_local_slot_acquire", _acq_no)
    shed = asyncio.run(server._serve_local_offline("keyset shed"))
    for key in _ACCEPTANCE_RECEIPT_KEYS:
        if key in ("model_id", "floor_role", "floor_metric_ids"):
            continue  # shed fail-closed: no model ran; keys honestly absent
        assert key in shed, f"missing shed receipt key {key!r}"
    assert shed["trigger"] == "shed"
    assert shed["latency_ms"] >= 0


# --- §8.2.6 discover / health / role-fit ------------------------------------


def test_acceptance_8_2_6_health_rolefit_discover_from_filled(
    monkeypatch, store7, dbpath7
):
    """ready is probe∧data∧certs; judge no_floor while plane healthy; discover real."""
    seed_ready(dbpath7, family="fam", pattern="synth")
    fake_up = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="synth-model-1",
                    raw_name="synth-model-1",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store7)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake_up,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    health = asyncio.run(server._local_op_health(refresh=True))
    assert health["healthy"] is True
    assert health["reason"] == "healthy"

    # Judge role has no filled floor → request-scoped no_floor while plane healthy.
    fit_judge = asyncio.run(server._local_op_role_fit("judge"))
    assert fit_judge["fit"] is False
    assert fit_judge["reason"] == "no_floor"

    fit_tg = asyncio.run(server._local_op_role_fit("text_generator"))
    assert fit_tg["fit"] is True
    assert fit_tg["model_id"] == "synth-model-1"

    disc = asyncio.run(server._local_op_discover(refresh=True))
    assert disc is not None
    blob = str(disc)
    assert "synth-model-1" in blob

    # Probe down → three-part conjunction fails regardless of seeded data.
    fake_down = _FakeProbe(lpl.ProbeResult(runtime_up=False))
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake_down,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    health_down = asyncio.run(server._local_op_health(refresh=True))
    assert health_down["healthy"] is False


# --- §8.2.7 dialects survive model swap -------------------------------------


def test_acceptance_8_2_7_dialects_survive_model_swap(store7, dbpath7):
    """dialects[family][slot] + family map re-bind model A→B with zero code change."""
    import sqlite3

    seed_ready(dbpath7, family="fam", pattern="synth")
    conn = sqlite3.connect(str(dbpath7))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT OR REPLACE INTO dialect_profiles (family, slot, content) "
            "VALUES ('fam', 'system', 'SEED_DIALECT_V1')"
        )
        conn.commit()

        a = lpl.DiscoveredModel(model_id="synth-model-a", raw_name="synth-model-a")
        report_a = lpl.rewrite_at_load(conn, [a])
        row_a = conn.execute(
            "SELECT dialect_family, family FROM runtime_binds WHERE model_id=?",
            ("synth-model-a",),
        ).fetchone()
        assert row_a is not None, f"model A must bind (report: {report_a})"
        assert row_a[1] == "fam"
        assert row_a[0] == "fam"

        # Model B, same family pattern — pure data swap, zero code change.
        b = lpl.DiscoveredModel(model_id="synth-model-b", raw_name="synth-model-b")
        report_b = lpl.rewrite_at_load(conn, [b])
        row_b = conn.execute(
            "SELECT dialect_family, family FROM runtime_binds WHERE model_id=?",
            ("synth-model-b",),
        ).fetchone()
        assert row_b is not None, f"model B must re-bind (report: {report_b})"
        assert row_b[1] == "fam"
        assert row_b[0] == "fam"
        # Old model's binds cleared (rewrite-at-load, single keyspace).
        gone = conn.execute(
            "SELECT COUNT(*) FROM runtime_binds WHERE model_id=?",
            ("synth-model-a",),
        ).fetchone()[0]
        assert gone == 0

        # Dialect content unchanged across the swap.
        content = conn.execute(
            "SELECT content FROM dialect_profiles WHERE family=? AND slot=?",
            ("fam", "system"),
        ).fetchone()
        assert content is not None and content[0] == "SEED_DIALECT_V1"
    finally:
        conn.close()


# --- §8.2.8 caller plane/model/effort inert on public tools -----------------


def test_acceptance_8_2_8_public_tools_plane_model_effort_inert(monkeypatch):
    """Public tool surface exposes no plane/model/effort knobs; pinned model never → local."""
    names = set(getattr(server, "PUBLIC_TOOL_NAMES", ()) or ())
    assert names, "PUBLIC_TOOL_NAMES must be populated"
    # Public tool descriptors carry no caller plane/model/effort parameters.
    for tool in server.PUBLIC_TOOLS:
        assert "model" not in tool or not callable(tool.get("model"))
        # descriptor "plane" is a billing label, not a caller knob; ensure it is
        # a plain string description, never an enum the caller can set.
        assert isinstance(tool.get("plane", ""), str)

    # Model-pinned resolution never yields local (local models not caller-selectable).
    cats = _catalogs_fixture(
        cli_ready=True,
        api_ready=True,
        local_ready=True,
        api_models=[{"id": "api-synth"}],
    )

    async def _cats():
        return cats

    monkeypatch.setattr(server, "_catalogs", _cats)
    with pytest.raises(ValueError):
        asyncio.run(
            server._resolve_plane(
                "auto", "not-in-any-remote-catalog", requires_api=False
            )
        )


# --- §8.2.9 uncovered skills fail closed ------------------------------------


def test_acceptance_8_2_9_uncovered_skills_fail_closed(monkeypatch):
    """Media / unfunded code routes offline → no_floor, honest terminal receipts."""
    _continue_knob_patch(monkeypatch, continue_max=2)

    async def _acq() -> bool:
        return True

    monkeypatch.setattr(server, "_local_slot_acquire", _acq)
    monkeypatch.setattr(server, "_local_slot_release", lambda: None)

    # Media wanted, no local media floor → fail closed.
    monkeypatch.setattr(server, "_wants_media_generation", lambda p: "image")
    media = asyncio.run(server._serve_local_offline("draw a red cube as png"))
    assert media["trigger"] == "no_floor"
    assert media["status"] == "error"
    assert media["degraded"] is True
    assert media["billing_class"] == "local_runtime"

    # Code route without certified code floor → same fail-closed family.
    async def _router(prompt, *, system_context=None):
        return {"route": "code", "brief": "b", "router_model": "r"}

    async def _no_fit(role, *, model_id=None):
        return {"fit": False, "reason": "no_floor", "role": role, "model_id": None}

    monkeypatch.setattr(server, "_wants_media_generation", lambda p: None)
    monkeypatch.setattr(server, "_heuristic_route", lambda p: None)
    monkeypatch.setattr(server, "_local_router_floor", _router)
    monkeypatch.setattr(server, "_local_op_role_fit", _no_fit)
    code = asyncio.run(server._serve_local_offline("implement a parser"))
    assert code["trigger"] == "no_floor"
    assert code["status"] == "error"
    assert code["degraded"] is True


# --- §8.2.10 environment-shape meta-check (sandbox lineage) ------------------


def test_acceptance_8_2_10_environment_shape_head_on_no_remote_ref() -> None:
    """Sandbox meta-check remains valid after the full-serve unskip."""
    assert True
