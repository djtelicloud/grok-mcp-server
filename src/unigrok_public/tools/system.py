"""System domain — health/ready/runtime payload builders (Phase 3 SRP).

No FastMCP registration here: server routes call these builders so HTTP shape
stays unit-testable without spinning the full gateway.
"""
from __future__ import annotations

from typing import Any


def healthz_body(*, service: str, version: str, layer: str = "public") -> dict[str, Any]:
    return {
        "status": "ok",
        "service": service,
        "version": version,
        "layer": layer or "public",
    }


def readyz_body(
    *,
    ready: bool,
    catalogs: dict[str, Any],
    bootstrap: dict[str, Any],
    state_ready: bool,
    cloudrun: bool = False,
) -> tuple[dict[str, Any], int]:
    """Return (json_body, http_status). Cloud Run keeps a minimal body."""
    status = "ready" if ready else "not_ready"
    code = 200 if ready else 503
    if cloudrun:
        return {"status": status}, code
    return {
        "status": status,
        "planes": catalogs,
        "bootstrap": bootstrap,
        "state": {"ready": state_ready, "backend": "sqlite"},
    }, code


def runtimez_core(
    *,
    service: str,
    version: str,
    layer: str,
    tool_count: int,
    state_persistence: Any,
    state_lifetime: Any,
    completion_recovery: Any,
) -> dict[str, Any]:
    """Stable identity fields for /runtimez (extended by server with live metrics)."""
    return {
        "service": service,
        "version": version,
        "mode": "public_core",
        "layer": layer or "public",
        "workspace_attached": False,
        "requires_project_files": False,
        "state_persistence": state_persistence,
        "state_lifetime": state_lifetime,
        "state_backend": "sqlite",
        "workspace_context_transport": "explicit_bounded_redacted_courier",
        "local_subagents": False,
        "completion_recovery": completion_recovery,
        "tool_count": tool_count,
        "mcp_endpoint": "/mcp",
        "needle_active": False,
    }
