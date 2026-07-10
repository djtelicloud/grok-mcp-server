from src.routing import (
    choose_model_candidate,
    classify_route,
    extract_routing_features,
    make_routing_receipt,
)
import pytest


def test_features_are_bounded_prompt_free_and_deterministic():
    prompt = "Implement and test src/router.py, then debug the failing pytest suite."
    first = extract_routing_features(prompt, reason_score=0)
    second = extract_routing_features(prompt, reason_score=0)
    assert first == second
    assert first["code_signal"] == 3
    assert first["estimated_input_tokens"] > 0
    assert len(first["feature_hash"]) == 12
    assert prompt not in str(first)


def test_image_content_hard_routes_to_vision():
    features = extract_routing_features(
        "Describe this",
        reason_score=0,
        input_messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:"}}]}],
    )
    assert features["has_image"] is True
    assert classify_route(mode="auto", thinking_mode=False, features=features) == (
        "vision", "vision_capability"
    )


def test_route_classes_cover_research_planning_coding_and_borderline():
    base = extract_routing_features("hello", reason_score=0)
    assert classify_route(mode="research", thinking_mode=False, features=base)[0] == "research"
    assert classify_route(mode="reasoning", thinking_mode=False, features=base)[0] == "planning"
    assert classify_route(mode="auto", thinking_mode=False, features=base)[0] == "coding"
    borderline = {**base, "reason_score": 1}
    assert classify_route(
        mode="auto", thinking_mode=False, features=borderline,
        borderline_prefers_planning=True,
    )[0] == "planning"


def test_planning_cold_start_prefers_grok_45_when_live():
    result = choose_model_candidate(
        "planning",
        available_models=["grok-4.5", "grok-4.3", "grok-4.20-0309-reasoning"],
    )
    assert result["model"] == "grok-4.5"
    assert result["selection_reason"] == "catalog_default"
    assert len(result["candidates"]) == 3


def test_catalog_removal_uses_next_compatible_candidate():
    result = choose_model_candidate("planning", available_models=["grok-4.3"])
    assert result["model"] == "grok-4.3"
    assert result["catalog_fallback"] is False


def test_mature_telemetry_requires_quality_margin_to_promote_peer():
    rows = [
        {"model": "grok-4.5", "samples": 25, "success_rate": 0.70, "avg_cost": 0.03},
        {"model": "grok-4.3", "samples": 25, "success_rate": 0.90, "avg_cost": 0.01},
    ]
    result = choose_model_candidate(
        "planning", available_models=["grok-4.5", "grok-4.3"], telemetry=rows
    )
    assert result["model"] == "grok-4.3"
    assert result["selection_reason"] == "telemetry_quality"


def test_immature_telemetry_cannot_displace_stable_default():
    rows = [
        {"model": "grok-4.5", "samples": 3, "success_rate": 0.0},
        {"model": "grok-4.3", "samples": 3, "success_rate": 1.0},
    ]
    result = choose_model_candidate(
        "planning", available_models=["grok-4.5", "grok-4.3"], telemetry=rows
    )
    assert result["model"] == "grok-4.5"


def test_coding_peer_can_win_on_mature_efficiency_without_quality_loss():
    rows = [
        {"model": "grok-build-0.1", "samples": 25, "success_rate": 0.91, "avg_cost": 0.02, "avg_latency": 10.0},
        {"model": "grok-4.20-0309-non-reasoning", "samples": 25, "success_rate": 0.90, "avg_cost": 0.01, "avg_latency": 5.0},
    ]
    result = choose_model_candidate(
        "coding",
        available_models=["grok-build-0.1", "grok-4.20-0309-non-reasoning"],
        telemetry=rows,
    )
    assert result["model"] == "grok-4.20-0309-non-reasoning"
    assert result["selection_reason"] == "telemetry_efficiency"


def test_calibration_precedes_conflicting_telemetry():
    calibration = [
        {"model": "grok-4.5", "n": 5, "success_rate": 0.60},
        {"model": "grok-4.3", "n": 5, "success_rate": 0.90},
    ]
    telemetry = [
        {"model": "grok-4.5", "samples": 30, "success_rate": 0.95},
        {"model": "grok-4.3", "samples": 30, "success_rate": 0.50},
    ]
    result = choose_model_candidate(
        "planning",
        available_models=["grok-4.5", "grok-4.3"],
        calibration=calibration,
        telemetry=telemetry,
    )
    assert result["model"] == "grok-4.3"
    assert result["evidence_source"] == "calibration"


def test_calibration_hold_blocks_conflicting_telemetry_promotion():
    calibration = [
        {"model": "grok-4.5", "n": 5, "success_rate": 0.95},
        {"model": "grok-4.3", "n": 5, "success_rate": 0.60},
    ]
    telemetry = [
        {"model": "grok-4.5", "samples": 30, "success_rate": 0.50},
        {"model": "grok-4.3", "samples": 30, "success_rate": 0.95},
    ]
    result = choose_model_candidate(
        "planning",
        available_models=["grok-4.5", "grok-4.3"],
        calibration=calibration,
        telemetry=telemetry,
    )
    assert result["model"] == "grok-4.5"
    assert result["selection_reason"] == "calibration_hold"


def test_receipt_contains_explanation_but_no_prompt():
    features = extract_routing_features("secret-ish prompt text", reason_score=2)
    receipt = make_routing_receipt(
        mode="auto",
        route_class="planning",
        model="grok-4.5",
        why="auto",
        why_detail="reasoning_score",
        features=features,
        candidates=[{"model": "grok-4.5", "rank": 0, "selected": True}],
        evidence_source="static",
        catalog_source="fixture",
    )
    assert receipt["v"] == 1
    assert receipt["resolved_model"] == "grok-4.5"
    assert "secret-ish" not in str(receipt)


@pytest.mark.asyncio
async def test_live_selector_uses_grok_45_and_emits_reason(monkeypatch):
    from src import utils

    utils._MODEL_RESOLVER.invalidate()
    monkeypatch.setattr(utils, "prefer_cli_when_api_key_missing", lambda: False)
    model, why, receipt, reasoning = await utils._select_routing_model(
        prompt="Audit this architecture and propose a strategy",
        mode="auto",
        thinking_mode=False,
        requested_model=None,
        active_store=None,
        input_messages=None,
        enable_agentic=True,
    )
    assert model == "grok-4.5"
    assert why == "auto"
    assert reasoning is True
    assert receipt["why_detail"] == "reasoning_score"
    assert receipt["resolved_model"] == model


@pytest.mark.asyncio
async def test_research_route_selects_multi_agent_capability(monkeypatch):
    from src import utils

    utils._MODEL_RESOLVER.invalidate()
    monkeypatch.setattr(utils, "prefer_cli_when_api_key_missing", lambda: False)
    model, why, receipt, _ = await utils._select_routing_model(
        prompt="Research current evidence",
        mode="research",
        thinking_mode=False,
        requested_model=None,
        active_store=None,
        input_messages=None,
        enable_agentic=True,
    )
    assert model == "grok-4.20-multi-agent-0309"
    assert why == "pin"
    assert receipt["route_class"] == "research"
    assert receipt["pin_source"] == "mode"


@pytest.mark.asyncio
async def test_explicit_model_short_circuits_catalog(monkeypatch):
    from src import utils

    monkeypatch.setattr(utils._MODEL_RESOLVER, "catalog_snapshot", pytest.fail)
    model, why, receipt, _ = await utils._select_routing_model(
        prompt="Anything",
        mode="auto",
        thinking_mode=False,
        requested_model="grok-4.3",
        active_store=None,
        input_messages=None,
        enable_agentic=True,
    )
    assert model == "grok-4.3"
    assert why == "pin"
    assert receipt["pin_source"] == "model"


@pytest.mark.asyncio
async def test_research_rejects_incompatible_explicit_pin(monkeypatch):
    from src import utils

    monkeypatch.setattr(utils._MODEL_RESOLVER, "catalog_snapshot", pytest.fail)
    with pytest.raises(ValueError, match="research mode requires"):
        await utils._select_routing_model(
            prompt="Research this",
            mode="research",
            thinking_mode=False,
            requested_model="grok-4.5",
            active_store=None,
            input_messages=None,
            enable_agentic=True,
        )
