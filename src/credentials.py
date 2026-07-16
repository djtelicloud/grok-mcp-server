"""Non-secret credential-plane status and agent action guidance.

This module deliberately knows nothing about SDK clients or credential values.
Callers provide only booleans and the bounded Grok CLI probe result, making the
same contract safe to expose through MCP discovery, status, and ``/runtimez``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Dict, Optional


UPSTREAM_PROVIDER_SECRET_ENV_NAMES = (
    "XAI_API_KEY",
    "XAI_MANAGEMENT_API_KEY",
    "XAI_MANAGEMENT_KEY",
    "GROK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)
AMBIENT_SERVER_SECRET_ENV_NAMES = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "AZURE_OPENAI_API_KEY",
    "NPM_TOKEN",
    "PYPI_API_TOKEN",
    "UNIGROK_MCP_TOKEN_SECRET",
    "UNIGROK_CLIENT_TOKEN",
    "MCP_TOKEN_SECRET",
    "SSH_AUTH_SOCK",
    "DOCKER_AUTH_CONFIG",
)
# These server-owned values must be scrubbed from Grok CLI subprocesses, but
# they are not single upstream provider bearer secrets: a credential-file path,
# the gateway client-token allowlist, and the principal-to-xAI-key JSON map.
NON_BEARER_SERVER_OWNED_ENV_NAMES = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "UNIGROK_API_KEYS",
    "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
)
SERVER_OWNED_SECRET_ENV_NAMES = (
    *UPSTREAM_PROVIDER_SECRET_ENV_NAMES,
    *AMBIENT_SERVER_SECRET_ENV_NAMES,
    *NON_BEARER_SERVER_OWNED_ENV_NAMES,
)

_SECRET_ENV_EXACT_NAMES = frozenset(
    {
        *SERVER_OWNED_SECRET_ENV_NAMES,
        "DATABASE_URL",
        "DB_URL",
        "POSTGRES_URL",
        "MYSQL_URL",
        "MONGODB_URI",
        "REDIS_URL",
        "SENTRY_DSN",
    }
)
_SECRET_ENV_SEGMENTS = frozenset(
    {"TOKEN", "SECRET", "PASSWORD", "PASSWD", "COOKIE", "CREDENTIAL"}
)
_SECRET_ENV_SUFFIXES = (
    "_API_KEY",
    "_PRIVATE_KEY",
    "_ACCESS_KEY",
    "_ACCESS_KEY_ID",
    "_AUTH_SOCK",
    "_DSN",
)


def is_secret_environment_name(name: str) -> bool:
    """Return whether an environment name denotes credential-bearing data.

    Exact canonical names cover current integrations. Conservative structural
    matching keeps unknown future provider/client secrets out of subprocesses
    by default without treating ordinary names such as TOKENIZERS_PARALLELISM
    as credentials.
    """

    normalized = str(name or "").strip().upper()
    if not normalized:
        return False
    if normalized in _SECRET_ENV_EXACT_NAMES or normalized.endswith(
        _SECRET_ENV_SUFFIXES
    ):
        return True
    return bool(_SECRET_ENV_SEGMENTS.intersection(normalized.split("_")))


def secret_environment_names(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return every canonical or secret-shaped name present in an env map."""

    source = os.environ if environ is None else environ
    return tuple(name for name in source if is_secret_environment_name(name))
_CLI_AUTH_ENV_UNSETS = " ".join(
    f"-u {name}" for name in SERVER_OWNED_SECRET_ENV_NAMES
)
CLI_AUTH_SETUP_COMMAND = (
    "docker exec -u 0 -it grok-mcp-server sh -lc "
    "'chown -R 1000:1000 /home/appuser/.grok && exec setpriv "
    "--reuid=1000 --regid=1000 --clear-groups env "
    f"{_CLI_AUTH_ENV_UNSETS} grok login --device-auth'"
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
