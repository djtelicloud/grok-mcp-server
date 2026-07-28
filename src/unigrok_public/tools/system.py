"""System domain — health/ready/runtime/status payload builders (Phase 3 SRP)."""
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


def list_models_body(
    *,
    catalogs: dict[str, Any],
    api_model_ids: list[str],
) -> dict[str, Any]:
    """Preserve public list_models shape (language catalog + media note)."""
    cli_models = list(catalogs["cli"].get("models", []))
    api_models = list(api_model_ids)
    return {
        "cli": {
            "ready": catalogs["cli"].get("ready", False),
            "models": cli_models,
            "default_model": catalogs["cli"].get("default_model"),
        },
        "api": {
            "configured": catalogs["api"].get("configured", False),
            "ready": catalogs["api"].get("ready", False),
            "models": api_models,
            "language_models": api_models,
            "image_models": [
                item["id"]
                for item in catalogs["api"].get("image_models", [])
                if item.get("id")
            ],
            "default_model": catalogs["api"].get("default_model"),
        },
        "all_model_ids": sorted(set(cli_models) | set(api_models)),
        "shared_model_ids": sorted(set(cli_models) & set(api_models)),
        "model_allowlist": None,
        "note": (
            "Media tools accept provider model ids directly; they are not restricted "
            "by this language-model catalog."
        ),
    }


def status_body(
    *,
    service: str,
    version: str,
    layer: str,
    tool_count: int,
    catalogs: dict[str, Any],
    description: dict[str, Any],
    state_ready: bool,
    telemetry: dict[str, Any],
    circuit_breakers: dict[str, Any],
    metered_api_enabled: bool,
    cloudrun: bool,
) -> dict[str, Any]:
    local = catalogs.get("local") or {
        "configured": False,
        "ready": False,
        "models": [],
        "default_model": None,
    }
    return {
        "service": service,
        "version": version,
        "mode": "public_core",
        "layer": layer or "public",
        "task_rag": description.get("task_rag"),
        "transport": "streamable_http",
        "mcp_endpoint": "/mcp",
        "workspace_attached": False,
        "requires_project_files": False,
        "tool_count": tool_count,
        "cli": catalogs["cli"],
        "api": catalogs["api"],
        "local": local,
        "bootstrap": description["bootstrap"],
        "credential_planes": description["credential_planes"],
        "api_spend_enforcement": {
            "owner_enabled": metered_api_enabled,
            "per_request_confirmation_required": False,
            "authorization_source": "server_owner_configuration",
        },
        "state": {
            "ready": state_ready,
            "backend": "sqlite",
            "sessions": True,
            "knowledge": True,
            "telemetry": True,
            "lifetime": ("instance_local" if cloudrun else "persistent_volume"),
        },
        "benchmark_summary": {
            key: telemetry[key]
            for key in (
                "sample_size",
                "verified_samples",
                "verified_success_rate",
                "latency_ms",
                "cost_usd",
                "callers",
                "models",
                "routes",
                "planes",
                "fallbacks",
            )
            if key in telemetry
        },
        "circuit_breakers": circuit_breakers,
        "needle_active": False,
    }


def benchmark_status_body(
    *, telemetry: dict[str, Any], circuit_breakers: dict[str, Any]
) -> dict[str, Any]:
    return {
        "telemetry": telemetry,
        "circuit_breakers": circuit_breakers,
        "routing_advisor": {
            "policy": "live_discovered_lead_with_provider_discovered_specialists",
            "automatic_model_experiments": False,
            "reason": (
                "Lead and specialists are selected from live provider catalogs; "
                "telemetry is observational."
            ),
        },
        "semantic_evaluation": {
            "mode": "explicit_feedback",
            "tool": "record_benchmark_result",
            "automatic_judge_spend": False,
        },
    }


def runtimez_merge(core: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Merge live metrics into the stable runtimez core without clobbering identity."""
    out = dict(core)
    out.update(extra)
    return out


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
