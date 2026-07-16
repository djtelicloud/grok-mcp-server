import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.credentials import (
    CLI_AUTH_NATIVE_SETUP_COMMAND,
    CLI_AUTH_SETUP_COMMAND,
    SERVER_OWNED_SECRET_ENV_NAMES,
    build_credential_plane_contract,
    credential_plane_policy,
    default_cli_auth_setup_command,
)


def test_cli_auth_setup_prefers_compose_auth_profile():
    assert CLI_AUTH_SETUP_COMMAND == "docker compose run --rm grok-cli-auth"
    assert default_cli_auth_setup_command(containerized=True) == CLI_AUTH_SETUP_COMMAND
    assert (
        default_cli_auth_setup_command(containerized=False)
        == CLI_AUTH_NATIVE_SETUP_COMMAND
    )


def test_cli_auth_setup_scrubs_server_credentials_but_keeps_oauth_path():
    for name in SERVER_OWNED_SECRET_ENV_NAMES:
        assert f"-u {name}" in CLI_AUTH_NATIVE_SETUP_COMMAND
    assert "-u GROK_AUTH_PATH" not in CLI_AUTH_NATIVE_SETUP_COMMAND
    assert "-u GOOGLE_CLOUD_PROJECT" not in CLI_AUTH_NATIVE_SETUP_COMMAND
    assert "grok login --device-auth" in CLI_AUTH_NATIVE_SETUP_COMMAND


def test_compose_cli_auth_scrubs_every_canonical_server_credential():
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    auth_service = compose.split("  grok-cli-auth:", 1)[1].split("\nvolumes:", 1)[0]

    for name in SERVER_OWNED_SECRET_ENV_NAMES:
        assert f"-u {name}" in auth_service
    assert "grok login --device-auth" in auth_service


READY_CLI = {
    "state": "ready",
    "ready": True,
    "binary": True,
    "auth": "oauth_verified",
    "setup_command": "docker exec auth",
}


def test_local_policy_defaults_cli_first_outside_tests(monkeypatch):
    monkeypatch.delenv("UNIGROK_PLANE_POLICY", raising=False)
    monkeypatch.delenv("UNI_GROK_TESTING", raising=False)
    assert credential_plane_policy(cloudrun=False) == "cli_first"
    assert credential_plane_policy(cloudrun=True) == "api_first"


def test_both_planes_ready_prefers_cli_without_notices(monkeypatch):
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status=READY_CLI,
    )
    assert contract["preferred_plane"] == "CLI"
    assert contract["effective_plane"] == "CLI"
    assert contract["service_usable"] is True
    assert contract["notices"] == []


def test_missing_cli_binary_prompts_for_approved_container_rebuild(monkeypatch):
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status={"state": "unavailable", "ready": False, "binary": False, "auth": "missing_binary"},
        containerized=True,
    )
    notice = contract["notices"][0]
    action = contract["cli"]["action"]
    assert notice["prompt_user"] is True
    assert notice["blocking"] is False
    assert action["kind"] == "rebuild_service"
    assert action["requires_user_approval"] is True
    assert action["command"] == "docker compose up --build -d grok-mcp"


def test_missing_cli_auth_prompts_for_device_flow(monkeypatch):
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status={
            "state": "needs_auth", "ready": False, "binary": True,
            "auth": "missing", "setup_command": "docker exec auth",
        },
    )
    action = contract["cli"]["action"]
    assert action["kind"] == "authenticate_cli"
    assert action["interactive"] is True
    assert action["command"] == "docker exec auth"


def test_default_cli_auth_action_uses_compose_auth_profile():
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status={
            "state": "needs_auth", "ready": False, "binary": True,
            "auth": "missing",
        },
        containerized=True,
    )
    command = contract["cli"]["action"]["command"]
    assert command == CLI_AUTH_SETUP_COMMAND
    assert command == "docker compose run --rm grok-cli-auth"
    assert "docker exec" not in command


def test_host_native_cli_auth_action_scrubs_server_secrets():
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status={
            "state": "needs_auth", "ready": False, "binary": True,
            "auth": "missing",
        },
        containerized=False,
    )
    command = contract["cli"]["action"]["command"]
    assert command == CLI_AUTH_NATIVE_SETUP_COMMAND
    assert "docker" not in command
    for name in SERVER_OWNED_SECRET_ENV_NAMES:
        assert f"-u {name}" in command


def test_missing_api_prompts_once_but_does_not_block_when_cli_can_serve(monkeypatch):
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    contract = build_credential_plane_contract(
        api_configured=False,
        cli_status=READY_CLI,
    )
    api_notice = next(item for item in contract["notices"] if item["plane"] == "API")
    assert contract["effective_plane"] == "CLI"
    assert api_notice["prompt_user"] is True
    assert api_notice["blocking"] is False
    assert api_notice["prompt_when"] == "now"
    assert contract["api"]["action"]["requires_user_secret"] is True
    assert "Never request the key in chat" in contract["api"]["action"]["instructions"]


def test_both_planes_missing_is_blocking_and_actionable(monkeypatch):
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    contract = build_credential_plane_contract(
        api_configured=False,
        cli_status={"state": "needs_auth", "ready": False, "binary": True, "auth": "missing"},
    )
    assert contract["service_usable"] is False
    assert contract["effective_plane"] is None
    assert {item["plane"] for item in contract["notices"] if item["blocking"]} == {"CLI", "API"}


def test_management_key_alone_never_satisfies_inference_readiness(monkeypatch):
    from src import utils

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "xai-management-only")
    monkeypatch.setattr(utils, "XAI_API_KEY", "")
    monkeypatch.setattr(utils, "_client", None)

    assert utils.xai_api_key_configured() is False
    contract = build_credential_plane_contract(
        api_configured=utils.xai_api_key_configured(),
        cli_status={
            "state": "needs_auth",
            "ready": False,
            "binary": True,
            "auth": "missing",
        },
    )
    assert contract["service_usable"] is False
    with pytest.raises(ValueError, match="XAI_API_KEY is not configured"):
        utils.get_xai_client()


def test_inference_key_alone_never_satisfies_management_readiness(monkeypatch):
    from src import rag, utils

    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setenv("XAI_API_KEY", "xai-inference-only")
    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.setattr(utils, "XAI_API_KEY", "xai-inference-only")
    monkeypatch.setattr(utils, "_client", None)
    monkeypatch.setattr("xai_sdk.Client", FakeClient)

    assert utils.xai_api_key_configured() is True
    assert rag.has_management_key() is False
    utils.get_xai_client()
    assert created == {
        "api_key": "xai-inference-only",
        "management_api_key": utils._XAI_INFERENCE_MANAGEMENT_ISOLATION_KEY,
    }


@pytest.mark.asyncio
async def test_cli_first_selector_uses_subscription_for_unpinned_planning(monkeypatch):
    from src import utils

    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    monkeypatch.setenv("UNI_GROK_TESTING", "0")
    monkeypatch.setenv("XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "cli_plane_ready_for_local_runtime", lambda: True)
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": ["grok-4.5", "grok-composer-2.5-fast"],
            "default_model": "grok-4.5",
        },
    )
    model, why, receipt, _ = await utils._select_routing_model(
        prompt="Plan a robust migration strategy",
        mode="auto",
        thinking_mode=False,
        requested_model=None,
        active_store=None,
        input_messages=None,
        enable_agentic=True,
    )
    assert model == "grok-4.5"
    assert why == "cost"
    assert receipt["why_detail"] == "cli_first_policy"
    assert receipt["catalog"]["source"] == "grok_cli_live"
    assert [item["model"] for item in receipt["candidates"]] == [
        "grok-4.5", "grok-composer-2.5-fast"
    ]


@pytest.mark.asyncio
async def test_cli_first_coding_uses_live_composer_not_retired_build(monkeypatch):
    from src import utils

    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    monkeypatch.setenv("UNI_GROK_TESTING", "0")
    monkeypatch.setenv("XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "cli_plane_ready_for_local_runtime", lambda: True)
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": ["grok-4.5", "grok-composer-2.5-fast"],
            "default_model": "grok-4.5",
        },
    )
    model, _, receipt, _ = await utils._select_routing_model(
        prompt="Reply with exactly OK.",
        mode="auto",
        thinking_mode=False,
        requested_model=None,
        active_store=None,
        input_messages=None,
        enable_agentic=False,
    )
    assert model == "grok-composer-2.5-fast"
    assert "grok-build" not in str(receipt)
    assert receipt["catalog"]["source"] == "grok_cli_live"


@pytest.mark.asyncio
async def test_cli_first_keeps_api_only_and_explicit_paths(monkeypatch):
    from src import utils

    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    monkeypatch.setenv("UNI_GROK_TESTING", "0")
    monkeypatch.setenv("XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "cli_plane_ready_for_local_runtime", lambda: True)
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": ["grok-4.5", "grok-composer-2.5-fast"],
            "default_model": "grok-4.5",
        },
    )

    pinned, _, pinned_receipt, _ = await utils._select_routing_model(
        prompt="Use 4.5", mode="auto", thinking_mode=False,
        requested_model="grok-4.5", active_store=None,
        input_messages=None, enable_agentic=True,
    )
    assert pinned == "grok-4.5"
    assert pinned_receipt["pin_source"] == "model"

    research, _, research_receipt, _ = await utils._select_routing_model(
        prompt="Research this", mode="research", thinking_mode=False,
        requested_model=None, active_store=None,
        input_messages=None, enable_agentic=True,
    )
    assert research.startswith("grok-4.20-multi-agent")
    assert research_receipt["route_class"] == "research"


@pytest.mark.asyncio
async def test_strict_plane_pin_uses_matching_live_catalog(monkeypatch):
    from src import utils

    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": ["grok-4.5", "grok-composer-2.5-fast"],
            "default_model": "grok-4.5",
        },
    )
    cli_model, _, cli_receipt, _ = await utils._select_routing_model(
        prompt="Use my subscription", mode="auto", thinking_mode=False,
        requested_model="grok-4.5", requested_plane="cli", active_store=None,
        input_messages=None, enable_agentic=True,
    )
    assert cli_model == "grok-4.5"
    assert cli_receipt["catalog"]["source"] == "grok_cli_live"

    monkeypatch.setattr(
        utils._MODEL_RESOLVER,
        "catalog_snapshot",
        AsyncMock(return_value=(["grok-4.5", "grok-build-0.1"], "xai_api", True)),
    )
    api_model, _, api_receipt, _ = await utils._select_routing_model(
        prompt="Use metered API", mode="auto", thinking_mode=False,
        requested_model="grok-build-0.1", requested_plane="api", active_store=None,
        input_messages=None, enable_agentic=True,
    )
    assert api_model == "grok-build-0.1"
    assert api_receipt["catalog"]["source"] == "xai_api"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected_source"),
    (("cli_first", "grok_cli_live"), ("api_first", "xai_api_live")),
)
async def test_auto_exact_pin_uses_live_plane_membership_and_policy(
    monkeypatch, policy, expected_source
):
    from src import utils

    shared_model = "grok-shared-exact"
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", policy)
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": [shared_model],
            "default_model": shared_model,
        },
    )
    monkeypatch.setattr(
        utils._MODEL_RESOLVER,
        "catalog_snapshot",
        AsyncMock(return_value=([shared_model], "xai_api_live", True)),
    )

    selected, _, receipt, _ = await utils._select_routing_model(
        prompt="Use the exact shared model",
        mode="auto",
        thinking_mode=False,
        requested_model=shared_model,
        requested_plane="auto",
        active_store=None,
        input_messages=None,
        enable_agentic=False,
    )

    assert selected == shared_model
    assert receipt["catalog"]["source"] == expected_source


@pytest.mark.asyncio
async def test_auto_exact_pin_keeps_thinking_on_api_under_cli_first(monkeypatch):
    from src import utils

    shared_model = "grok-shared-exact"
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": [shared_model],
            "default_model": shared_model,
        },
    )
    monkeypatch.setattr(
        utils._MODEL_RESOLVER,
        "catalog_snapshot",
        AsyncMock(return_value=([shared_model], "xai_api_live", True)),
    )

    selected, _, receipt, _ = await utils._select_routing_model(
        prompt="Think deeply with the exact shared model",
        mode="auto",
        thinking_mode=True,
        requested_model=shared_model,
        requested_plane="auto",
        active_store=None,
        input_messages=None,
        enable_agentic=True,
    )

    assert selected == shared_model
    assert receipt["catalog"]["source"] == "xai_api_live"


@pytest.mark.asyncio
async def test_auto_exact_pin_keeps_thinking_on_api_when_discovery_fails(monkeypatch):
    from src import utils

    shared_model = "grok-shared-exact"
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "cli_first")
    monkeypatch.setenv("XAI_API_KEY", "configured")
    monkeypatch.setattr(utils, "XAI_API_KEY", "configured")
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": [shared_model],
            "default_model": shared_model,
        },
    )
    monkeypatch.setattr(
        utils._MODEL_RESOLVER,
        "catalog_snapshot",
        AsyncMock(return_value=([shared_model], "static_fallback", False)),
    )

    _, _, receipt, _ = await utils._select_routing_model(
        prompt="Think deeply while discovery is unavailable",
        mode="auto",
        thinking_mode=True,
        requested_model=shared_model,
        requested_plane="auto",
        active_store=None,
        input_messages=None,
        enable_agentic=True,
    )

    assert receipt["catalog"]["source"] == "static_fallback"


@pytest.mark.asyncio
async def test_auto_exact_pin_routes_cli_only_live_slug_to_cli(monkeypatch):
    from src import utils

    cli_model = "grok-cli-only-exact"
    monkeypatch.setenv("UNIGROK_PLANE_POLICY", "api_first")
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": [cli_model],
            "default_model": cli_model,
        },
    )
    monkeypatch.setattr(
        utils._MODEL_RESOLVER,
        "catalog_snapshot",
        AsyncMock(return_value=(["grok-api-only"], "xai_api_live", True)),
    )

    _, _, receipt, _ = await utils._select_routing_model(
        prompt="Use the exact CLI model",
        mode="auto",
        thinking_mode=False,
        requested_model=cli_model,
        requested_plane="auto",
        active_store=None,
        input_messages=None,
        enable_agentic=False,
    )

    assert receipt["catalog"]["source"] == "grok_cli_live"


@pytest.mark.asyncio
async def test_strict_cli_rejects_api_only_model(monkeypatch):
    from src import utils

    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            **READY_CLI,
            "models": ["grok-4.5", "grok-composer-2.5-fast"],
            "default_model": "grok-4.5",
        },
    )
    with pytest.raises(ValueError, match="not available on the authenticated CLI"):
        await utils._select_routing_model(
            prompt="Pin API coding model", mode="auto", thinking_mode=False,
            requested_model="grok-build-0.1", requested_plane="cli", active_store=None,
            input_messages=None, enable_agentic=True,
        )


@pytest.mark.asyncio
async def test_strict_cli_rejects_exact_pin_for_thinking(monkeypatch):
    from src import utils

    with pytest.raises(ValueError, match="API-only"):
        await utils._select_routing_model(
            prompt="Think deeply",
            mode="auto",
            thinking_mode=True,
            requested_model="grok-4.5",
            requested_plane="cli",
            active_store=None,
            input_messages=None,
            enable_agentic=True,
        )


@pytest.mark.asyncio
async def test_direct_router_rejects_api_only_route_on_strict_cli():
    from src import utils

    with pytest.raises(ValueError, match="API-only"):
        await utils._select_routing_model(
            prompt="Research current evidence", mode="research", thinking_mode=False,
            requested_model=None, requested_plane="cli", active_store=None,
            input_messages=None, enable_agentic=True,
        )


@pytest.mark.asyncio
async def test_agent_turn_returns_actions_when_both_planes_are_missing(monkeypatch):
    from src import utils

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(utils, "XAI_API_KEY", "")
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: {
            "state": "needs_auth", "ready": False, "binary": True,
            "auth": "missing", "setup_command": "docker exec auth",
        },
    )
    monkeypatch.setattr(utils, "get_dynamic_context", AsyncMock(return_value=("", False, None)))
    layer = await utils.run_agent_turn(prompt="hello")
    assert layer.finish_reason == "error"
    assert layer.route == "credential-setup"
    assert layer.credentials["service_usable"] is False
    assert "neither credential plane" in layer.generation


@pytest.mark.asyncio
async def test_api_only_request_blocks_with_secure_key_action(monkeypatch):
    from src import utils

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(utils, "XAI_API_KEY", "")
    monkeypatch.setattr(
        utils,
        "grok_cli_plane_status",
        lambda **_: READY_CLI,
    )
    monkeypatch.setattr(utils, "get_dynamic_context", AsyncMock(return_value=("", False, None)))
    layer = await utils.run_agent_turn(prompt="Research current evidence", mode="research")
    api_notice = next(item for item in layer.credentials["notices"] if item["plane"] == "API")
    assert layer.finish_reason == "error"
    assert layer.route == "credential-setup"
    assert api_notice["blocking"] is True
    assert api_notice["prompt_user"] is True
    assert "never request the key in chat" in api_notice["message"].lower()


@pytest.mark.asyncio
async def test_cross_plane_policy_allows_unready_cli_start_to_reach_supervisor(
    monkeypatch,
):
    from src import utils

    credentials = {
        "service_usable": True,
        "cli": {"available": False},
        "api": {"available": True},
        "notices": [],
    }
    monkeypatch.setattr(utils, "credential_plane_contract", lambda *_: credentials)
    monkeypatch.setattr(utils, "grok_cli_plane_status", lambda **_: {})
    monkeypatch.setattr(
        utils, "get_dynamic_context", AsyncMock(return_value=("", False, None))
    )
    mock_orchestrate = AsyncMock(
        return_value=utils.MetaLayer(
            generation="recovered", plane="API", finish_reason="fallback"
        )
    )
    monkeypatch.setattr(utils, "orchestrate", mock_orchestrate)

    layer = await utils.run_agent_turn(
        prompt="complete the task",
        plane="cli",
        fallback_policy="cross_plane",
    )

    assert layer.generation == "recovered"
    assert mock_orchestrate.await_args.kwargs["requested_plane"] == "cli"
    assert mock_orchestrate.await_args.kwargs["fallback_policy"] == "cross_plane"


@pytest.mark.asyncio
async def test_cross_plane_policy_allows_unready_api_start_to_reach_supervisor(
    monkeypatch,
):
    from src import utils

    credentials = {
        "service_usable": True,
        "cli": {"available": True},
        "api": {"available": False},
        "notices": [],
    }
    monkeypatch.setattr(utils, "credential_plane_contract", lambda *_: credentials)
    monkeypatch.setattr(utils, "grok_cli_plane_status", lambda **_: {})
    monkeypatch.setattr(
        utils, "get_dynamic_context", AsyncMock(return_value=("", False, None))
    )
    mock_orchestrate = AsyncMock(
        return_value=utils.MetaLayer(
            generation="recovered", plane="CLI-Fallback", finish_reason="fallback"
        )
    )
    monkeypatch.setattr(utils, "orchestrate", mock_orchestrate)

    layer = await utils.run_agent_turn(
        prompt="complete the task",
        plane="api",
        fallback_policy="cross_plane",
    )

    assert layer.generation == "recovered"
    assert mock_orchestrate.await_args.kwargs["requested_plane"] == "api"
    assert mock_orchestrate.await_args.kwargs["fallback_policy"] == "cross_plane"


@pytest.mark.asyncio
async def test_run_agent_turn_crosses_cli_preflight_for_api_only_research(
    monkeypatch,
):
    """The public boundary must execute the real selector, not preflight-block."""
    from src import utils

    research_model = "grok-4.20-multi-agent"
    cli_status = {
        **READY_CLI,
        "models": ["grok-composer-2.5-fast"],
        "default_model": "grok-composer-2.5-fast",
    }
    credentials = {
        "service_usable": True,
        "cli": {"available": True},
        "api": {"available": True},
        "notices": [],
    }
    monkeypatch.setattr(utils, "credential_plane_contract", lambda *_: credentials)
    monkeypatch.setattr(utils, "grok_cli_plane_status", lambda **_: cli_status)
    monkeypatch.setattr(
        utils, "get_dynamic_context", AsyncMock(return_value=("", False, None))
    )
    monkeypatch.setattr(
        utils._MODEL_RESOLVER,
        "catalog_snapshot",
        AsyncMock(return_value=([research_model], "xai_api_live", True)),
    )
    mock_call = AsyncMock(return_value=("research result", 20, 0.01, False))
    monkeypatch.setattr(utils, "_call_plane", mock_call)

    layer = await utils.run_agent_turn(
        prompt="Research current evidence",
        model=research_model,
        mode="research",
        enable_agentic=False,
        plane="cli",
        fallback_policy="cross_plane",
    )

    assert mock_call.await_count == 1
    assert layer.generation == "research result"
    assert layer.finish_reason == "fallback"
    assert layer.routing_receipt["authority"] == "grok"
    assert layer.routing_receipt["requested_plane"] == "CLI"
    assert layer.routing_receipt["resolved_plane"] == "API"
    assert layer.routing_receipt["why_detail"] == "selection_plane_fallback"


@pytest.mark.asyncio
async def test_strict_cli_api_only_preflight_keeps_grok_authority(monkeypatch):
    from src import utils

    credentials = {
        "service_usable": True,
        "cli": {"available": True},
        "api": {"available": True},
        "notices": [],
    }
    monkeypatch.setattr(utils, "credential_plane_contract", lambda *_: credentials)
    monkeypatch.setattr(utils, "grok_cli_plane_status", lambda **_: READY_CLI)
    monkeypatch.setattr(
        utils, "get_dynamic_context", AsyncMock(return_value=("", False, None))
    )

    layer = await utils.run_agent_turn(
        prompt="Research current evidence",
        mode="research",
        plane="cli",
        fallback_policy="same_plane",
    )

    assert layer.finish_reason == "error"
    assert layer.route == "plane-validation"
    assert layer.routing_receipt["provider"] == "xai"
    assert layer.routing_receipt["authority"] == "grok"
    assert layer.routing_receipt["why_detail"] == (
        "same_plane_capability_incompatible"
    )
    assert layer.routing_receipt["attempts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_detail", "request_kwargs", "credentials"),
    (
        (
            "credentials_unavailable",
            {},
            {
                "service_usable": False,
                "cli": {"available": False},
                "api": {"available": False},
                "notices": [],
            },
        ),
        (
            "same_plane_capability_incompatible",
            {"mode": "research", "plane": "cli", "fallback_policy": "same_plane"},
            {
                "service_usable": True,
                "cli": {"available": True},
                "api": {"available": True},
                "notices": [],
            },
        ),
        (
            "cli_credentials_unavailable",
            {"plane": "cli", "fallback_policy": "same_plane"},
            {
                "service_usable": True,
                "cli": {"available": False},
                "api": {"available": True},
                "notices": [],
            },
        ),
        (
            "api_credentials_unavailable",
            {"plane": "api", "fallback_policy": "same_plane"},
            {
                "service_usable": True,
                "cli": {"available": True},
                "api": {
                    "available": False,
                    "action": {"kind": "configure_api_key"},
                },
                "notices": [{"plane": "API"}],
            },
        ),
    ),
)
async def test_preflight_failures_persist_grok_session_and_telemetry(
    monkeypatch, tmp_path, expected_detail, request_kwargs, credentials
):
    from src import utils

    test_store = utils.GrokSessionStore(
        db_path=tmp_path / f"{expected_detail}.db"
    )
    monkeypatch.setattr(utils, "store", test_store)
    monkeypatch.setattr(
        utils, "credential_plane_contract", lambda *_: credentials
    )
    monkeypatch.setattr(utils, "grok_cli_plane_status", lambda **_: READY_CLI)
    monkeypatch.setattr(
        utils, "get_dynamic_context", AsyncMock(return_value=("", False, "ctx"))
    )
    mock_compact = AsyncMock()
    monkeypatch.setattr(utils, "maybe_compact_history", mock_compact)
    try:
        layer = await utils.run_agent_turn(
            prompt="preserve this objective",
            session=f"preflight-{expected_detail}",
            enable_agentic=False,
            **request_kwargs,
        )

        assert layer.routing_receipt["why_detail"] == expected_detail
        rows = await test_store.get_telemetry_stats()
        assert len(rows) == 1
        metadata = json.loads(rows[0]["metadata"])
        assert metadata["routing"] == layer.routing_receipt
        history = await utils.load_history(
            f"preflight-{expected_detail}", test_store
        )
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert history[0]["content"] == "preserve this objective"
        assert history[1]["metadata"]["routing"] == layer.routing_receipt
        mock_compact.assert_not_awaited()
    finally:
        await test_store.close()
