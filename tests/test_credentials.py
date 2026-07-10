from unittest.mock import AsyncMock

import pytest

from src.credentials import (
    CLI_AUTH_SETUP_COMMAND,
    build_credential_plane_contract,
    credential_plane_policy,
)


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


def test_default_cli_auth_action_repairs_volume_then_drops_privileges():
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status={
            "state": "needs_auth", "ready": False, "binary": True,
            "auth": "missing",
        },
    )
    command = contract["cli"]["action"]["command"]
    assert command == CLI_AUTH_SETUP_COMMAND
    assert "docker exec -u 0 -it grok-mcp-server" in command
    assert "chown -R 1000:1000 /home/appuser/.grok" in command
    assert "setpriv --reuid=1000 --regid=1000 --clear-groups" in command


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
