"""Non-secret credential-plane status and agent action guidance.

This module deliberately knows nothing about SDK clients or credential values.
Callers provide only booleans and the bounded Grok CLI probe result, making the
same contract safe to expose through MCP discovery, status, and ``/runtimez``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


CLI_AUTH_SETUP_COMMAND = (
    "docker exec -it grok-mcp-server env -u XAI_API_KEY -u GROK_API_KEY "
    "grok login --device-auth"
)
CLI_DOCKER_REBUILD_COMMAND = "docker compose up --build -d grok-mcp"
CLI_NATIVE_INSTALL_COMMAND = "curl -fsSL https://x.ai/cli/install.sh | bash"
SERVICE_RECREATE_COMMAND = "docker compose up -d --force-recreate grok-mcp"


def credential_plane_policy(*, cloudrun: bool = False) -> str:
    """Return the bounded plane preference.

    Local UniGrok favors the subscription-backed CLI for compatible, unpinned
    work. Cloud Run cannot provide the machine OAuth plane and stays API-first.
    Operators can explicitly choose ``api_first`` when API-native behavior is
    more important than subscription utilization.
    """

    raw = os.environ.get("UNIGROK_PLANE_POLICY", "").strip().lower()
    if raw in {"cli_first", "api_first"}:
        return raw
    if os.environ.get("UNI_GROK_TESTING") == "1":
        return "api_first"
    return "api_first" if cloudrun else "cli_first"


def _api_action() -> Dict[str, Any]:
    return {
        "id": "configure_xai_api_key",
        "kind": "configure_secret",
        "requires_user_approval": True,
        "requires_user_secret": True,
        "interactive": True,
        "secret_name": "XAI_API_KEY",
        "scope": "unigrok_service",
        "instructions": (
            "Ask permission to help configure XAI_API_KEY in the global UniGrok "
            "service .env. Never request the key in chat, store it in the caller's "
            "project, or echo it. After the user enters it through a secure local "
            "prompt/editor, recreate the service and verify discovery/status."
        ),
        "restart_command": SERVICE_RECREATE_COMMAND,
    }


def _cli_action(cli: Dict[str, Any], *, containerized: bool) -> Dict[str, Any]:
    state = str(cli.get("state") or "unreachable")
    binary = bool(cli.get("binary"))
    if not binary:
        return {
            "id": "install_grok_cli",
            "kind": "rebuild_service" if containerized else "install_cli",
            "requires_user_approval": True,
            "requires_user_secret": False,
            "interactive": False,
            "command": CLI_DOCKER_REBUILD_COMMAND if containerized else CLI_NATIVE_INSTALL_COMMAND,
            "instructions": (
                "The official UniGrok Docker image includes the pinned Grok CLI; ask "
                "permission to rebuild the service image."
                if containerized
                else "Ask permission before installing the official xAI Grok CLI."
            ),
        }
    if state in {"needs_auth", "api_key_conflict"}:
        return {
            "id": "authenticate_grok_cli",
            "kind": "authenticate_cli",
            "requires_user_approval": True,
            "requires_user_secret": False,
            "interactive": True,
            "command": str(cli.get("setup_command") or CLI_AUTH_SETUP_COMMAND),
            "instructions": (
                "Ask permission to run the device-auth command, then let the user "
                "complete the browser/device confirmation. Recheck plane health afterward."
            ),
        }
    return {
        "id": "repair_grok_cli",
        "kind": "diagnose_cli",
        "requires_user_approval": True,
        "requires_user_secret": False,
        "interactive": False,
        "instructions": (
            "Explain that the CLI probe is currently unreachable. Ask permission to "
            "inspect service logs and retry status before changing authentication."
        ),
    }


def build_credential_plane_contract(
    *,
    api_configured: bool,
    cli_status: Optional[Dict[str, Any]],
    cloudrun: bool = False,
    containerized: bool = False,
) -> Dict[str, Any]:
    """Build one versioned, prompt-ready credential-plane contract."""

    cli = dict(cli_status or {})
    cli_state = str(cli.get("state") or "unreachable")
    cli_ready = bool(cli.get("ready", cli_state == "ready")) and not cloudrun
    api_ready = bool(api_configured)
    policy = credential_plane_policy(cloudrun=cloudrun)
    effective = "CLI" if cli_ready and policy == "cli_first" else "API" if api_ready else "CLI" if cli_ready else None

    api_view: Dict[str, Any] = {
        "state": "configured" if api_ready else "missing",
        "available": api_ready,
        "credential": "server_environment",
        "secret_name": "XAI_API_KEY",
    }
    if not api_ready:
        api_view["action"] = _api_action()

    cli_view: Dict[str, Any] = {
        "state": cli_state if cli_status else ("disabled" if cloudrun else "unreachable"),
        "available": cli_ready,
        "binary": bool(cli.get("binary", cli_ready)),
        "auth": str(cli.get("auth") or "unverified"),
        "credential": "grok_com_oauth",
    }
    if not cli_ready and not cloudrun:
        cli_view["action"] = _cli_action(cli, containerized=containerized)

    notices = []
    if not cli_ready and not cloudrun:
        action = cli_view["action"]
        notices.append({
            "id": f"cli:{cli_view['state']}:{cli_view['auth']}",
            "plane": "CLI",
            "severity": "warning" if api_ready else "error",
            "blocking": not api_ready,
            "prompt_user": True,
            "message": (
                "The preferred Grok CLI subscription plane is unavailable. "
                "Ask the user for permission before running the recommended repair."
            ),
            "action_id": action["id"],
        })
    if not api_ready:
        notices.append({
            "id": "api:missing:XAI_API_KEY",
            "plane": "API",
            "severity": "info" if cli_ready else "error",
            "blocking": not cli_ready,
            "prompt_user": True,
            "prompt_when": "now",
            "message": (
                "The xAI API plane is not configured. Ask permission to help configure "
                "the global UniGrok service secret when an API-only capability is needed."
            ),
            "action_id": "configure_xai_api_key",
        })

    return {
        "version": 1,
        "policy": policy,
        "preferred_plane": "CLI" if policy == "cli_first" else "API",
        "effective_plane": effective,
        "service_usable": api_ready or cli_ready,
        "degraded": not (api_ready and (cli_ready or cloudrun)),
        "api": api_view,
        "cli": cli_view,
        "notices": notices,
        "notice_behavior": "Prompt once per notice id; prompt again only after state changes.",
        "local_usage": {
            "cli_requests_tracked": True,
            "cli_fields": ["requests", "success", "latency", "model", "caller", "route", "estimated_tokens"],
            "cli_provider_quota_available": False,
            "cli_provider_cost_available": False,
            "api_response_cost_exact": True,
        },
    }
