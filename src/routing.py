"""Deterministic, explainable model selection for UniGrok.

The selector is deliberately local-first.  It extracts a small bounded feature
vector, chooses a capability class, filters a tiny ordered candidate set against
the cached xAI catalog, and only lets mature local evidence displace the stable
default.  It never asks another model which model to use.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROUTING_RECEIPT_VERSION = 1
QUALITY_MARGIN = 0.15
TELEMETRY_MIN_SAMPLES = 20
CALIBRATION_MIN_SAMPLES = 5

ROUTE_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "planning": ("grok-4.5", "grok-4.3", "grok-4.20-0309-reasoning"),
    "coding": ("grok-build-0.1", "grok-4.20-0309-non-reasoning", "grok-4.3"),
    "vision": ("grok-4.5", "grok-4.3"),
    "research": ("grok-4.20-multi-agent-0309", "grok-4.20-multi-agent"),
}

_CODE_TERMS = {
    "bug", "code", "commit", "compile", "debug", "function", "implement",
    "lint", "patch", "pytest", "refactor", "repository", "test", "typescript",
}
_CODE_SUFFIX_RE = re.compile(
    r"\.(?:c|cc|cpp|css|go|html|java|js|json|jsx|md|php|py|rb|rs|sh|sql|swift|ts|tsx|yaml|yml)\b",
    re.IGNORECASE,
)
_TOOL_TERMS = {"browse", "execute", "file", "git", "inspect", "run", "search", "test", "tool"}


def _term_score(text: str, terms: Iterable[str], cap: int = 3) -> int:
    lowered = text.lower()
    hits = sum(1 for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered))
    return min(cap, hits)


def _messages_have_image(messages: Optional[Sequence[Dict[str, Any]]]) -> bool:
    if not messages:
        return False
    stack = list(messages)
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            kind = current.get("type")
            if kind:
                kind = str(kind).lower()
                if kind in {"image", "image_url", "input_image"}:
                    return True
            if "image_url" in current:
                return True
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return False


def extract_routing_features(
    prompt: str,
    *,
    reason_score: int,
    input_messages: Optional[Sequence[Dict[str, Any]]] = None,
    enable_agentic: bool = True,
) -> Dict[str, Any]:
    """Return a compact prompt-free feature vector safe for telemetry."""
    text = str(prompt or "")
    code_signal = _term_score(text, _CODE_TERMS)
    if _CODE_SUFFIX_RE.search(text):
        code_signal = min(3, code_signal + 1)
    tool_intensity = _term_score(text, _TOOL_TERMS, cap=2)
    if enable_agentic and len(text) > 280:
        tool_intensity = min(2, tool_intensity + 1)
    features: Dict[str, Any] = {
        "reason_score": max(0, int(reason_score or 0)),
        "code_signal": code_signal,
        "tool_intensity": tool_intensity,
        "estimated_input_tokens": max(1, (len(text) + 3) // 4),
        "has_image": _messages_have_image(input_messages),
    }
    canonical = json.dumps(features, sort_keys=True, separators=(",", ":"))
    features["feature_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return features


def classify_route(
    *,
    mode: str,
    thinking_mode: bool,
    features: Dict[str, Any],
    borderline_prefers_planning: Optional[bool] = None,
) -> Tuple[str, str]:
    """Choose a bounded capability class and a human-readable reason code."""
    if mode == "research":
        return "research", "research_capability"
    if features.get("has_image"):
        return "vision", "vision_capability"
    if thinking_mode or mode == "reasoning":
        return "planning", "explicit_reasoning_mode"
    reason_score = int(features.get("reason_score") or 0)
    code_signal = int(features.get("code_signal") or 0)
    if reason_score >= 2:
        return "planning", "reasoning_score"
    if reason_score == 1:
        if borderline_prefers_planning:
            return "planning", "borderline_evidence"
        return "coding", "borderline_static"
    if code_signal >= 2:
        return "coding", "coding_signal"
    return "coding", "simple_default"


def _aggregate_stats(rows: Sequence[Dict[str, Any]], model: str) -> Dict[str, Any]:
    samples = 0
    successes = 0.0
    cost = 0.0
    latency = 0.0
    for row in rows:
        if str(row.get("model") or "") != model:
            continue
        n = max(0, int(row.get("samples") or row.get("n") or 0))
        if not n:
            continue
        samples += n
        successes += float(row.get("success_rate") or 0.0) * n
        cost += float(row.get("avg_cost") or row.get("avg_cost_usd") or 0.0) * n
        latency += float(row.get("avg_latency") or 0.0) * n
    return {
        "samples": samples,
        "success_rate": successes / samples if samples else None,
        "avg_cost": cost / samples if samples else None,
        "avg_latency": latency / samples if samples and latency else None,
    }


def choose_model_candidate(
    route_class: str,
    *,
    available_models: Optional[Sequence[str]],
    telemetry: Sequence[Dict[str, Any]] = (),
    calibration: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """Pick one of at most three route candidates with stable hysteresis.

    The first available candidate is the cold-start default.  A peer may
    displace it only when both have mature calibration or telemetry and the
    peer's success rate clears QUALITY_MARGIN.  This margin is the hysteresis:
    ordinary noise cannot flap the route between releases or restarts.
    """
    ordered = list(ROUTE_CANDIDATES.get(route_class, ROUTE_CANDIDATES["planning"]))[:3]
    available = set(available_models or [])
    candidates = [model for model in ordered if not available or model in available]
    catalog_fallback = False
    if not candidates:
        candidates = [ordered[0]]
        catalog_fallback = True

    selected = candidates[0]
    selection_reason = "catalog_default"
    evidence_source = "static"

    def apply_evidence(rows: Sequence[Dict[str, Any]], min_samples: int, source: str) -> bool:
        nonlocal selected, selection_reason, evidence_source
        incumbent = _aggregate_stats(rows, selected)
        if incumbent["samples"] < min_samples:
            return False
        eligible = []
        for model in candidates:
            stats = _aggregate_stats(rows, model)
            if stats["samples"] >= min_samples and stats["success_rate"] is not None:
                eligible.append((model, stats))
        if len(eligible) < 2:
            return False
        best = selected
        best_rate = float(incumbent["success_rate"] or 0.0)
        for model, stats in eligible:
            rate = float(stats["success_rate"] or 0.0)
            if rate >= best_rate + QUALITY_MARGIN:
                best, best_rate = model, float(rate)
        if best != selected:
            selected = best
            selection_reason = f"{source}_quality"
        else:
            selection_reason = f"{source}_hold"
            if source == "telemetry" and route_class == "coding":
                incumbent_cost = incumbent.get("avg_cost")
                incumbent_latency = incumbent.get("avg_latency")
                efficient = None
                efficient_gain = 0.0
                for model, stats in eligible:
                    if model == selected:
                        continue
                    rate = float(stats["success_rate"] or 0.0)
                    if rate < best_rate - 0.03:
                        continue
                    gains = []
                    if incumbent_cost and stats.get("avg_cost") is not None:
                        gains.append(1.0 - float(stats["avg_cost"]) / float(incumbent_cost))
                    if incumbent_latency and stats.get("avg_latency") is not None:
                        gains.append(1.0 - float(stats["avg_latency"]) / float(incumbent_latency))
                    gain = max(gains or [0.0])
                    if gain >= 0.30 and gain > efficient_gain:
                        efficient, efficient_gain = model, gain
                if efficient:
                    selected = efficient
                    selection_reason = "telemetry_efficiency"
        evidence_source = source
        return True

    if not apply_evidence(calibration, CALIBRATION_MIN_SAMPLES, "calibration"):
        apply_evidence(telemetry, TELEMETRY_MIN_SAMPLES, "telemetry")

    receipt_candidates = []
    for rank, model in enumerate(candidates):
        stats = _aggregate_stats(telemetry, model)
        cal = _aggregate_stats(calibration, model)
        receipt_candidates.append({
            "model": model,
            "rank": rank,
            "selected": model == selected,
            "telemetry_samples": stats["samples"],
            "telemetry_success_rate": stats["success_rate"],
            "telemetry_avg_cost": stats["avg_cost"],
            "telemetry_avg_latency": stats["avg_latency"],
            "calibration_samples": cal["samples"],
            "calibration_success_rate": cal["success_rate"],
        })
    return {
        "model": selected,
        "selection_reason": selection_reason,
        "evidence_source": evidence_source,
        "catalog_fallback": catalog_fallback,
        "candidates": receipt_candidates,
    }


def make_routing_receipt(
    *,
    mode: str,
    route_class: str,
    model: str,
    why: str,
    why_detail: str,
    features: Dict[str, Any],
    candidates: Sequence[Dict[str, Any]],
    evidence_source: str,
    catalog_source: str,
    pin_source: Optional[str] = None,
    catalog_fallback: bool = False,
) -> Dict[str, Any]:
    return {
        "v": ROUTING_RECEIPT_VERSION,
        "mode": mode,
        "route_class": route_class,
        "resolved_model": model,
        "why": why,
        "why_detail": why_detail,
        "pin_source": pin_source,
        "features": dict(features),
        "candidates": list(candidates)[:3],
        "evidence_source": evidence_source,
        "catalog": {"source": catalog_source, "fallback": bool(catalog_fallback)},
    }
