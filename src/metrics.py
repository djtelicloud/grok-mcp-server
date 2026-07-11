"""Structured usage metrics for the MCP status tool and HTTP diagnostics.

The local telemetry ledger is authoritative for requests that passed through
UniGrok.  xAI API billing is exact per response; Grok CLI subscription usage is
observable locally but has no provider cost/quota API.  Optional Management API
data is therefore a separate team-level comparison, never merged into CLI data.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx


_PROVIDER_CACHE: Dict[str, Any] = {"key": None, "at": 0.0, "value": None}
_PROVIDER_CACHE_TTL_SEC = 300.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def telemetry_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("metadata")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        import json

        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _plane_name(value: Any) -> str:
    text = str(value or "unknown").strip()
    lowered = text.lower()
    if lowered == "api":
        return "API"
    if lowered == "cli":
        return "CLI"
    if lowered in ("cli-fallback", "cli_fallback"):
        return "CLI-Fallback"
    return text or "unknown"


def _created_at(row: Dict[str, Any]) -> Optional[datetime]:
    raw = str(row.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _today_rows(rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    local_now = now or datetime.now()
    return [row for row in rows if (created := _created_at(row)) and created.date() == local_now.date()]


def _aggregate(rows: List[Dict[str, Any]], plane: Optional[str] = None) -> Dict[str, Any]:
    selected = [row for row in rows if plane is None or _plane_name(row.get("chosen_plane")) == plane]
    latencies = sorted(_safe_float(row.get("latency")) for row in selected)
    successes = sum(1 for row in selected if _safe_int(row.get("success")) == 1)
    api_cost = sum(
        _safe_float(row.get("cost"))
        for row in selected
        if _plane_name(row.get("chosen_plane")) == "API"
    )
    token_total = 0
    exact_token_rows = 0
    estimated_token_rows = 0
    models: Dict[str, int] = {}
    route_classes: Dict[str, int] = {}
    selection_reasons: Dict[str, int] = {}
    routing_receipt_rows = 0
    caller_attributed_rows = 0
    semantic_rows = 0
    semantic_sums = {"correctness": 0.0, "tool_efficiency": 0.0, "safety": 0.0, "overall": 0.0}
    semantic_judge_cost = 0.0
    for row in selected:
        meta = telemetry_metadata(row)
        if str(meta.get("caller") or "").strip():
            caller_attributed_rows += 1
        semantic = meta.get("semantic")
        if isinstance(semantic, dict):
            semantic_rows += 1
            scores = semantic.get("scores")
            scores = scores if isinstance(scores, dict) else {}
            for key in ("correctness", "tool_efficiency", "safety"):
                semantic_sums[key] += _safe_float(scores.get(key))
            semantic_sums["overall"] += _safe_float(semantic.get("overall"))
            semantic_judge_cost += _safe_float(semantic.get("judge_cost_usd"))
        tokens = max(0, _safe_int(meta.get("tokens")))
        token_total += tokens
        token_kind = str(meta.get("token_kind") or "").lower()
        if tokens and token_kind == "provider_exact":
            exact_token_rows += 1
        elif tokens:
            estimated_token_rows += 1
        model = str(meta.get("model") or "").strip()
        if model:
            models[model] = models.get(model, 0) + 1
        routing = meta.get("routing")
        if isinstance(routing, dict):
            routing_receipt_rows += 1
            route_class = str(routing.get("route_class") or "").strip()
            reason = str(routing.get("why_detail") or routing.get("why") or "").strip()
            if route_class:
                route_classes[route_class] = route_classes.get(route_class, 0) + 1
            if reason:
                selection_reasons[reason] = selection_reasons.get(reason, 0) + 1

    request_count = len(selected)
    p95_index = min(int(request_count * 0.95), request_count - 1) if request_count else 0
    return {
        "requests": request_count,
        "successful_requests": successes,
        "success_rate": successes / request_count if request_count else None,
        "avg_latency_sec": sum(latencies) / request_count if request_count else None,
        "p95_latency_sec": latencies[p95_index] if request_count else None,
        "api_cost_usd": api_cost,
        "tracked_tokens": token_total,
        "exact_token_requests": exact_token_rows,
        "estimated_token_requests": estimated_token_rows,
        "models": dict(sorted(models.items(), key=lambda item: (-item[1], item[0]))),
        "route_classes": dict(sorted(route_classes.items(), key=lambda item: (-item[1], item[0]))),
        "selection_reasons": dict(sorted(selection_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "routing_receipt_requests": routing_receipt_rows,
        "caller_attributed_requests": caller_attributed_rows,
        # Shadow semantic-eval scores (observational only — routing never
        # consumes these). Averages are None when no row was graded, matching
        # success_rate's zero-row convention.
        "semantic": {
            "scored_requests": semantic_rows,
            "avg_correctness": semantic_sums["correctness"] / semantic_rows if semantic_rows else None,
            "avg_tool_efficiency": semantic_sums["tool_efficiency"] / semantic_rows if semantic_rows else None,
            "avg_safety": semantic_sums["safety"] / semantic_rows if semantic_rows else None,
            "avg_overall": semantic_sums["overall"] / semantic_rows if semantic_rows else None,
            "judge_cost_usd": semantic_judge_cost,
        },
    }


def _recent_routes(rows: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """Newest prompt-free routing receipts for the UI.

    Telemetry intent/prompt excerpts are intentionally excluded.  Only the
    already-bounded receipt plus operational result fields leave storage.
    """
    result: List[Dict[str, Any]] = []
    # Store rows are newest-first (get_telemetry_stats ORDER BY id DESC).
    for row in rows:
        meta = telemetry_metadata(row)
        routing = meta.get("routing")
        if not isinstance(routing, dict):
            continue
        result.append({
            "created_at": row.get("created_at"),
            "caller": meta.get("caller"),
            "plane": _plane_name(row.get("chosen_plane")),
            "success": _safe_int(row.get("success")) == 1,
            "latency_sec": _safe_float(row.get("latency")),
            "cost_usd": _safe_float(row.get("cost")) if _plane_name(row.get("chosen_plane")) == "API" else None,
            "tokens": max(0, _safe_int(meta.get("tokens"))),
            "routing": routing,
        })
        if len(result) >= max(1, int(limit or 12)):
            break
    return result


def aggregate_telemetry_planes(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Backward-compatible lifetime aggregates used by /metrics/Prometheus."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_plane_name(row.get("chosen_plane")), []).append(row)

    result: Dict[str, Dict[str, Any]] = {}
    for plane, plane_rows in grouped.items():
        summary = _aggregate(plane_rows)
        result[plane] = {
            "requests": summary["requests"],
            "success_rate": summary["success_rate"] or 0.0,
            "avg_latency_sec": summary["avg_latency_sec"] or 0.0,
            "p95_latency_sec": summary["p95_latency_sec"] or 0.0,
            "total_cost_usd": summary["api_cost_usd"],
        }
    return result


def aggregate_telemetry_callers(
    rows: List[Dict[str, Any]], *, limit: int = 20
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        caller = str(telemetry_metadata(row).get("caller") or "").strip()
        if caller:
            grouped.setdefault(caller, []).append(row)
    ranked = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:limit]
    result: Dict[str, Dict[str, Any]] = {}
    for caller, caller_rows in ranked:
        successes = sum(1 for row in caller_rows if _safe_int(row.get("success")) == 1)
        result[caller] = {
            "requests": len(caller_rows),
            "success_rate": successes / len(caller_rows),
            "total_cost_usd": sum(_safe_float(row.get("cost")) for row in caller_rows),
        }
    return result


async def fetch_provider_api_usage() -> Dict[str, Any]:
    """Optionally fetch today's team-wide API spend from xAI Management API."""
    enabled = os.environ.get("UNIGROK_PROVIDER_USAGE", "auto").strip().lower()
    management_key = os.environ.get("XAI_MANAGEMENT_API_KEY", "").strip()
    team_id = os.environ.get("UNIGROK_XAI_TEAM_ID", "").strip()
    base = {
        "scope": "xai_api_team",
        "period": "today_utc",
        "usage_usd": None,
        "source": "xai_management_api",
    }
    if enabled in ("0", "false", "off", "disabled"):
        return {**base, "state": "disabled", "detail": "Provider comparison disabled."}
    if not management_key or not team_id:
        return {
            **base,
            "state": "not_configured",
            "detail": (
                "Optional organization-wide API billing comparison is off. "
                "UniGrok still tracks its own API requests and exact response cost locally; "
                "no additional setup is required."
            ),
        }

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    cache_key = (team_id, day)
    if (
        _PROVIDER_CACHE.get("key") == cache_key
        and _PROVIDER_CACHE.get("value") is not None
        and time.monotonic() - float(_PROVIDER_CACHE.get("at") or 0.0) < _PROVIDER_CACHE_TTL_SEC
    ):
        return dict(_PROVIDER_CACHE["value"])

    payload = {
        "analyticsRequest": {
            "timeRange": {
                "startTime": f"{day} 00:00:00",
                "endTime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "Etc/GMT",
            },
            "timeUnit": "TIME_UNIT_DAY",
            "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
            "groupBy": ["description"],
            "filters": [],
        }
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                f"https://management-api.x.ai/v1/billing/teams/{team_id}/usage",
                headers={"Authorization": f"Bearer {management_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        usage_usd = 0.0
        for series in body.get("timeSeries", []):
            for point in series.get("dataPoints", []):
                values = point.get("values") or []
                if values:
                    usage_usd += float(values[0] or 0.0)
        result = {
            **base,
            "state": "ready",
            "usage_usd": usage_usd,
            "limit_reached": bool(body.get("limitReached")),
            "detail": "Team-wide API billing; may include API calls outside UniGrok.",
        }
    except httpx.HTTPStatusError as exc:
        result = {**base, "state": "error", "detail": f"Management API returned HTTP {exc.response.status_code}."}
    except Exception as exc:
        result = {**base, "state": "error", "detail": f"Provider comparison failed: {type(exc).__name__}."}

    _PROVIDER_CACHE.update({"key": cache_key, "at": time.monotonic(), "value": dict(result)})
    return result


def build_metrics_snapshot(
    rows: List[Dict[str, Any]],
    *,
    runtime: Optional[Dict[str, Any]] = None,
    circuit_breakers: Optional[Dict[str, Any]] = None,
    routing_advisor: Optional[Dict[str, Any]] = None,
    provider_api: Optional[Dict[str, Any]] = None,
    semantic_evals: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    caller_limit: int = 20,
) -> Dict[str, Any]:
    today = _today_rows(rows, now=now)
    today_planes = sorted({_plane_name(row.get("chosen_plane")) for row in today})
    lifetime_planes = sorted({_plane_name(row.get("chosen_plane")) for row in rows})
    return {
        "format": "unigrok-json-v1",
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "planes": aggregate_telemetry_planes(rows),
        "callers": aggregate_telemetry_callers(rows, limit=caller_limit),
        "runtime": runtime or {},
        "circuit_breakers": circuit_breakers or {},
        "routing_advisor": routing_advisor,
        "semantic_evals": semantic_evals,
        "usage": {
            "today": {
                "summary": _aggregate(today),
                "planes": {plane: _aggregate(today, plane) for plane in today_planes},
                "callers": aggregate_telemetry_callers(today, limit=caller_limit),
                "recent_routes": _recent_routes(today),
            },
            "lifetime": {
                "summary": _aggregate(rows),
                "planes": {plane: _aggregate(rows, plane) for plane in lifetime_planes},
                "callers": aggregate_telemetry_callers(rows, limit=caller_limit),
                "recent_routes": _recent_routes(rows),
            },
            "api_billing": {
                "local_source": "exact_xai_response_cost",
                "provider": provider_api or {
                    "state": "not_checked",
                    "usage_usd": None,
                    "scope": "xai_api_team",
                },
            },
            "cli_subscription": {
                "provider_usage_available": False,
                "cost_per_request_usd": None,
                "detail": (
                    "SuperGrok subscription quota/spend is not exposed by the xAI API. "
                    "UniGrok reports only CLI requests it observed locally; activity outside UniGrok is invisible."
                ),
            },
            "data_quality": {
                "telemetry_rows": len(rows),
                "model_attributed_rows": sum(1 for row in rows if telemetry_metadata(row).get("model")),
                "token_attributed_rows": sum(
                    1 for row in rows if _safe_int(telemetry_metadata(row).get("tokens")) > 0
                ),
                "routing_receipt_rows": sum(
                    1 for row in rows if isinstance(telemetry_metadata(row).get("routing"), dict)
                ),
                "semantic_scored_rows": sum(
                    1 for row in rows if isinstance(telemetry_metadata(row).get("semantic"), dict)
                ),
            },
        },
    }
