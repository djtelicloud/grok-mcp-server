from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from src.providers import (
    ADCIdentity,
    AnthropicAdapter,
    CredentialState,
    GeminiAdapter,
    GrokSupervisorBinding,
    OpenAIAdapter,
    ProviderAdapter,
    ProviderChannel,
    ProviderConfigurationError,
    ProviderId,
    ProviderMessage,
    ProviderRequest,
    ProviderTransportError,
    RouteClass,
    VertexADCAdapter,
    build_provider_registry,
    load_google_adc_identity,
)
from src.providers.base import MAX_PROVIDER_RESPONSE_BYTES, opaque_fingerprint
from src.providers.config import load_model_pins
from src.providers.errors import ProviderProtocolError


def normalized_request(**overrides) -> ProviderRequest:
    values = {
        "request_id": "req-provider-1",
        "supervision": GrokSupervisorBinding(
            session_id="session-1",
            objective_id="objective-1",
            route_decision_id="route-1",
            ttl_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        ),
        "route": RouteClass.PLANNING,
        "messages": (
            ProviderMessage(role="system", content="Be concise."),
            ProviderMessage(role="user", content="Explain the result."),
        ),
        "max_output_tokens": 32_768,
        "timeout_seconds": 120.0,
    }
    values.update(overrides)
    return ProviderRequest(**values)


def supervisor_binding(ttl_expires_at: datetime) -> GrokSupervisorBinding:
    return GrokSupervisorBinding(
        session_id="session-1",
        objective_id="objective-1",
        route_decision_id="route-1",
        ttl_expires_at=ttl_expires_at,
    )


def async_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_request_contract_is_strict_and_bounded():
    legacy_list = normalized_request(
        messages=[ProviderMessage(role="user", content="legacy Python caller")]
    )
    assert isinstance(legacy_list.messages, tuple)

    with pytest.raises(ValidationError):
        ProviderRequest(
            request_id="req-1",
            supervision=normalized_request().supervision,
            route=RouteClass.CODING,
            messages=(ProviderMessage(role="assistant", content="no user"),),
        )
    with pytest.raises(ValidationError):
        ProviderMessage(role="tool", content="not normalized")
    with pytest.raises(ValidationError):
        ProviderRequest(
            request_id="req-1",
            supervision=normalized_request().supervision,
            route=RouteClass.CODING,
            messages=(ProviderMessage(role="user", content="hello"),),
            max_output_tokens=32_769,
        )
    with pytest.raises(ValidationError):
        ProviderRequest(
            request_id="req-1",
            supervision=normalized_request().supervision,
            route=RouteClass.CODING,
            messages=(ProviderMessage(role="user", content="hello"),),
            unexpected=True,
        )


def test_grok_supervisor_binding_and_worker_authority_are_fail_closed():
    request = normalized_request()
    assert request.supervision.supervisor == "grok"
    with pytest.raises(ValidationError):
        GrokSupervisorBinding(
            supervisor="openai",
            session_id="session-1",
            objective_id="objective-1",
            route_decision_id="route-1",
            ttl_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        GrokSupervisorBinding(
            session_id="session-1",
            objective_id="objective-1",
            route_decision_id="route-1",
            ttl_expires_at=datetime(2030, 1, 1),
        )


@pytest.mark.asyncio
async def test_text_only_adapters_refuse_vision_until_media_contract_exists():
    result = await GeminiAdapter(environ={"GEMINI_API_KEY": "unused"}).attempt(
        normalized_request(route=RouteClass.VISION)
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "unsupported_route"
    assert result.failure.provider == ProviderId.GOOGLE
    assert result.failure.channel == ProviderChannel.GEMINI_API_KEY


@pytest.mark.asyncio
async def test_expired_ttl_blocks_credentials_adc_and_http_effects():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    request = normalized_request(supervision=supervisor_binding(now))
    http_calls = 0
    adc_calls = 0

    async def http_handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        raise AssertionError("expired work reached HTTP")

    async def adc_provider() -> ADCIdentity:
        nonlocal adc_calls
        adc_calls += 1
        raise AssertionError("expired work reached ADC")

    client = async_client(http_handler)
    try:
        api_result = await OpenAIAdapter(
            client=client,
            environ={},
            clock=lambda: now,
        ).attempt(request)
        vertex_result = await VertexADCAdapter(
            client=client,
            environ={"UNIGROK_VERTEX_PROJECT": "valid-project"},
            token_provider=adc_provider,
            clock=lambda: now,
        ).attempt(request)
    finally:
        await client.aclose()

    assert api_result.status == "failed"
    assert api_result.failure is not None
    assert api_result.failure.error_code == "ttl_expired"
    assert vertex_result.status == "failed"
    assert vertex_result.failure is not None
    assert vertex_result.failure.error_code == "ttl_expired"
    assert http_calls == 0
    assert adc_calls == 0


@pytest.mark.asyncio
async def test_timeout_is_capped_to_remaining_ttl_and_ttl_is_model_visible():
    now = datetime(2029, 12, 31, 23, 59, 55, tzinfo=UTC)
    deadline = now + timedelta(seconds=2.5)
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["timeouts"] = request.extensions["timeout"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_ttl",
                "model": "gpt-5.1",
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": "bounded"}]}],
            },
        )

    client = async_client(handler)
    try:
        await OpenAIAdapter(
            client=client,
            environ={"OPENAI_API_KEY": "key"},
            clock=lambda: now,
        ).complete(normalized_request(supervision=supervisor_binding(deadline)))
    finally:
        await client.aclose()

    numeric_timeouts = [
        value for value in observed["timeouts"].values() if value is not None
    ]
    assert numeric_timeouts
    assert max(numeric_timeouts) == pytest.approx(2.5)
    assert observed["payload"]["input"][0] == {
        "role": "system",
        "content": (
            "Supervisor TTL expires at 2029-12-31T23:59:57Z; "
            "do not claim work after it."
        ),
    }


@pytest.mark.asyncio
async def test_ttl_is_rechecked_after_adc_before_inference_http():
    started = datetime(2030, 1, 1, tzinfo=UTC)
    current = [started]
    http_calls = 0

    async def token_provider() -> ADCIdentity:
        current[0] = started + timedelta(seconds=3)
        return ADCIdentity(
            access_token=SecretStr("token-never-sent"),
            project_id="valid-project",
        )

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        raise AssertionError("expired post-ADC work reached inference HTTP")

    client = async_client(handler)
    try:
        result = await VertexADCAdapter(
            client=client,
            environ={},
            token_provider=token_provider,
            clock=lambda: current[0],
        ).attempt(
            normalized_request(
                supervision=supervisor_binding(started + timedelta(seconds=2))
            )
        )
    finally:
        await client.aclose()
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "ttl_expired"
    assert http_calls == 0


@pytest.mark.asyncio
async def test_stalled_adc_is_cancelled_by_absolute_attempt_deadline():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    cancelled = False
    http_calls = 0

    async def stalled_token_provider() -> ADCIdentity:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True
        raise AssertionError("unreachable")

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        raise AssertionError("stalled ADC reached inference HTTP")

    client = async_client(handler)
    started = time.monotonic()
    try:
        result = await VertexADCAdapter(
            client=client,
            environ={},
            token_provider=stalled_token_provider,
            clock=lambda: now,
        ).attempt(
            normalized_request(
                supervision=supervisor_binding(now + timedelta(seconds=0.05))
            )
        )
    finally:
        await client.aclose()
    assert time.monotonic() - started < 0.5
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "ttl_expired"
    assert cancelled is True
    assert http_calls == 0


@pytest.mark.asyncio
async def test_slow_progress_stream_cannot_extend_absolute_attempt_deadline():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    chunks_sent = 0

    class TrickleStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal chunks_sent
            while True:
                await asyncio.sleep(0.01)
                chunks_sent += 1
                yield b" "

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=TrickleStream())

    client = async_client(handler)
    started = time.monotonic()
    try:
        result = await OpenAIAdapter(
            client=client,
            environ={"OPENAI_API_KEY": "key"},
            clock=lambda: now,
        ).attempt(
            normalized_request(
                supervision=supervisor_binding(now + timedelta(seconds=0.06))
            )
        )
    finally:
        await client.aclose()
    assert time.monotonic() - started < 0.5
    assert chunks_sent >= 1
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "ttl_expired"


@pytest.mark.asyncio
async def test_ttl_is_rechecked_at_response_emission_boundary():
    started = datetime(2030, 1, 1, tzinfo=UTC)
    deadline = started + timedelta(seconds=10)
    current = [started]

    async def handler(_: httpx.Request) -> httpx.Response:
        current[0] = deadline
        return httpx.Response(
            200,
            json={
                "id": "late_response",
                "model": "gpt-5.1",
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": "late"}]}],
            },
        )

    client = async_client(handler)
    try:
        result = await OpenAIAdapter(
            client=client,
            environ={"OPENAI_API_KEY": "key"},
            clock=lambda: current[0],
        ).attempt(normalized_request(supervision=supervisor_binding(deadline)))
    finally:
        await client.aclose()
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "ttl_expired"


def test_model_pin_precedence_and_secret_safe_invalid_value():
    pins = load_model_pins(
        ProviderChannel.OPENAI_API,
        {
            "UNIGROK_OPENAI_MODEL": "gpt-5-mini",
            "UNIGROK_OPENAI_PLANNING_MODEL": "gpt-5.1",
        },
    )
    assert pins.planning == "gpt-5.1"
    assert pins.coding == "gpt-5-mini"

    bad = "invalid model fake-secret-value"
    with pytest.raises(ProviderConfigurationError) as caught:
        load_model_pins(
            ProviderChannel.OPENAI_API,
            {"UNIGROK_OPENAI_CODING_MODEL": bad},
        )
    assert bad not in str(caught.value)
    assert "UNIGROK_OPENAI_CODING_MODEL" in str(caught.value)


@pytest.mark.asyncio
async def test_openai_fixed_endpoint_bounded_payload_and_receipt():
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["Authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_openai_1",
                "model": "gpt-5.1-2026-01-01",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "OpenAI answer"}],
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
            },
        )

    client = async_client(handler)
    try:
        adapter = OpenAIAdapter(
            client=client,
            environ={
                "OPENAI_API_KEY": "openai-test-secret",
                "OPENAI_BASE_URL": "https://attacker.invalid",
            },
        )
        response = await adapter.complete(normalized_request())
    finally:
        await client.aclose()

    assert observed["url"] == "https://api.openai.com/v1/responses"
    assert observed["authorization"] == "Bearer openai-test-secret"
    assert observed["payload"]["max_output_tokens"] == 16_384
    assert observed["payload"]["store"] is False
    assert response.text == "OpenAI answer"
    assert response.finish_reason == "stop"
    assert response.model == "gpt-5.1-2026-01-01"
    assert response.receipt.usage.total_tokens == 19
    assert response.receipt.usage.source == "provider_exact"
    receipt = response.receipt.model_dump_json()
    assert "openai-test-secret" not in receipt
    assert "attacker.invalid" not in receipt


@pytest.mark.asyncio
async def test_openai_refusal_and_length_are_normalized():
    responses = iter(
        [
            {
                "id": "resp_refusal",
                "model": "gpt-5.1",
                "status": "completed",
                "output": [
                    {"content": [{"type": "refusal", "refusal": "Cannot comply."}]}
                ],
            },
            {
                "id": "resp_length",
                "model": "gpt-5.1",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"content": [{"type": "output_text", "text": "Partial"}]}],
            },
            {
                "id": "resp_unknown",
                "model": "gpt-5.1",
                "status": "unexpected",
                "output": [
                    {"content": [{"type": "output_text", "text": "Unverified"}]}
                ],
            },
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    client = async_client(handler)
    try:
        adapter = OpenAIAdapter(client=client, environ={"OPENAI_API_KEY": "key"})
        refusal = await adapter.attempt(normalized_request())
        length = await adapter.attempt(normalized_request(request_id="req-provider-2"))
        unknown = await adapter.attempt(normalized_request(request_id="req-provider-3"))
    finally:
        await client.aclose()
    assert refusal.status == "returned"
    assert refusal.response is not None
    assert refusal.response.finish_reason == "content_filter"
    assert length.status == "returned"
    assert length.response is not None
    assert length.response.finish_reason == "length"
    assert unknown.status == "returned"
    assert unknown.response is not None
    assert unknown.response.finish_reason == "unknown"


@pytest.mark.asyncio
async def test_missing_or_unsafe_upstream_model_is_explicit_requested_fallback():
    unsafe_model = "unsafe model with secret-model-fragment"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_model_fallback",
                "model": unsafe_model,
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": "answer"}]}],
                "usage": {"input_tokens": 2, "total_tokens": 2},
            },
        )

    client = async_client(handler)
    try:
        response = await OpenAIAdapter(
            client=client,
            environ={"OPENAI_API_KEY": "key"},
        ).complete(normalized_request(model="gpt-5.1"))
    finally:
        await client.aclose()
    assert response.model == "gpt-5.1"
    assert response.receipt.model_source == "requested_fallback"
    assert response.receipt.usage.source == "partial"
    assert response.receipt.usage.output_tokens is None
    assert unsafe_model not in response.model_dump_json()


def test_invalid_usage_metadata_is_explicitly_partial_not_exact():
    usage = OpenAIAdapter(environ={})._usage(-1, True, "not-a-count")
    assert usage.source == "partial"
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None


@pytest.mark.asyncio
async def test_attempt_returns_output_or_secret_safe_failure_to_grok():
    secret = "provider-secret-never-return"

    async def returned_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "model": "gpt-5.1",
                "status": "completed",
                "output": [
                    {"content": [{"type": "output_text", "text": "worker output"}]}
                ],
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            },
        )

    returned_client = async_client(returned_handler)
    try:
        returned = await OpenAIAdapter(
            client=returned_client, environ={"OPENAI_API_KEY": secret}
        ).attempt(normalized_request())
    finally:
        await returned_client.aclose()
    assert returned.status == "returned"
    assert returned.response is not None
    assert returned.response.text == "worker output"
    assert returned.response.receipt.supervision.session_id == "session-1"
    assert returned.response.authority.may_finalize is False
    assert returned.response.authority.may_route is False
    assert returned.response.receipt.cost_usd is None
    assert returned.response.receipt.cost_source == "unavailable"

    # Missing credentials is the deterministic failure path; no live call occurs.
    missing = await OpenAIAdapter(environ={}).attempt(normalized_request())
    assert missing.status == "failed"
    assert missing.failure is not None
    assert missing.failure.error_kind == "configuration"
    assert missing.failure.error_code == "credential_missing"
    assert missing.failure.supervision.objective_id == "objective-1"
    assert missing.failure.authority.may_verify is False
    assert secret not in missing.model_dump_json()


@pytest.mark.asyncio
async def test_anthropic_accepts_both_key_names_with_canonical_precedence():
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["key"] = request.headers["x-api-key"]
        observed["version"] = request.headers["anthropic-version"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_claude_1",
                "model": "claude-fable-5",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Claude answer"}],
                "usage": {"input_tokens": 8, "output_tokens": 5},
            },
        )

    client = async_client(handler)
    try:
        adapter = AnthropicAdapter(
            client=client,
            environ={
                "ANTHROPIC_API_KEY": "canonical-key",
                "CLAUDE_API_KEY": "alias-key",
                "ANTHROPIC_BASE_URL": "https://attacker.invalid",
            },
        )
        response = await adapter.complete(normalized_request())
    finally:
        await client.aclose()
    assert observed["url"] == "https://api.anthropic.com/v1/messages"
    assert observed["key"] == "canonical-key"
    assert observed["version"] == "2023-06-01"
    assert observed["payload"]["system"] == (
        "Supervisor TTL expires at 2030-01-01T00:00:00Z; "
        "do not claim work after it.\n\nBe concise."
    )
    assert observed["payload"]["messages"] == [
        {"role": "user", "content": "Explain the result."}
    ]
    assert response.text == "Claude answer"
    assert response.receipt.usage.total_tokens == 13
    assert response.receipt.usage.source == "derived"


@pytest.mark.asyncio
async def test_anthropic_claude_key_alias_works_by_itself():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "claude-alias"
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-sonnet-5",
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": "partial"}],
            },
        )

    client = async_client(handler)
    try:
        adapter = AnthropicAdapter(
            client=client, environ={"CLAUDE_API_KEY": "claude-alias"}
        )
        response = await adapter.complete(normalized_request(route=RouteClass.CODING))
    finally:
        await client.aclose()
    assert response.model == "claude-sonnet-5"
    assert response.finish_reason == "length"
    assert response.receipt.usage.source == "unavailable"
    assert response.receipt.usage.input_tokens is None


@pytest.mark.asyncio
async def test_anthropic_rejects_unsupported_temperature_before_http():
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("unsupported temperature reached HTTP")

    client = async_client(handler)
    try:
        with pytest.raises(ProviderConfigurationError) as failed:
            await AnthropicAdapter(
                client=client,
                environ={"ANTHROPIC_API_KEY": "key"},
            ).complete(normalized_request(temperature=0.2))
    finally:
        await client.aclose()
    assert failed.value.code == "temperature_unsupported"
    assert calls == 0


@pytest.mark.asyncio
async def test_gemini_fixed_endpoint_excludes_thought_parts():
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["key"] = request.headers["x-goog-api-key"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "responseId": "gemini_response_1",
                "modelVersion": "gemini-3.5-flash",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"thought": True, "text": "hidden reasoning"},
                                {"text": "Gemini answer"},
                            ]
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 14,
                },
            },
        )

    client = async_client(handler)
    try:
        adapter = GeminiAdapter(
            client=client,
            environ={
                "GEMINI_API_KEY": "gemini-test-secret",
                "GEMINI_BASE_URL": "https://attacker.invalid",
            },
        )
        response = await adapter.complete(normalized_request())
    finally:
        await client.aclose()
    assert observed["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash:generateContent"
    )
    assert observed["key"] == "gemini-test-secret"
    assert observed["payload"]["systemInstruction"] == {
        "parts": [
            {
                "text": (
                    "Supervisor TTL expires at 2030-01-01T00:00:00Z; "
                    "do not claim work after it."
                )
            },
            {"text": "Be concise."},
        ]
    }
    assert observed["payload"]["generationConfig"]["maxOutputTokens"] == 32_768
    assert response.text == "Gemini answer"
    assert "hidden reasoning" not in response.text
    assert "gemini-test-secret" not in response.receipt.model_dump_json()


@pytest.mark.asyncio
async def test_gemini_safety_block_is_a_valid_empty_filtered_response():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "promptFeedback": {"blockReason": "SAFETY"},
                "usageMetadata": {"promptTokenCount": 3, "totalTokenCount": 3},
            },
        )

    client = async_client(handler)
    try:
        result = await GeminiAdapter(
            client=client, environ={"GEMINI_API_KEY": "key"}
        ).complete(normalized_request())
    finally:
        await client.aclose()
    assert result.text == ""
    assert result.finish_reason == "content_filter"


@pytest.mark.asyncio
async def test_vertex_adc_uses_fixed_regional_endpoint_and_opaque_project_receipt():
    observed = {}

    async def token_provider() -> ADCIdentity:
        return ADCIdentity(
            access_token=SecretStr("vertex-access-token"),
            project_id="identity-project",
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "responseId": "vertex_response_1",
                "modelVersion": "gemini-3.5-flash",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "Vertex answer"}]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 9,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 12,
                },
            },
        )

    client = async_client(handler)
    try:
        adapter = VertexADCAdapter(
            client=client,
            environ={
                "UNIGROK_VERTEX_PROJECT": "configured-project",
                "UNIGROK_VERTEX_LOCATION": "us-central1",
                "VERTEX_BASE_URL": "https://attacker.invalid",
            },
            token_provider=token_provider,
        )
        response = await adapter.complete(normalized_request())
    finally:
        await client.aclose()
    assert observed["url"] == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/"
        "configured-project/locations/us-central1/publishers/google/models/"
        "gemini-3.5-flash:generateContent"
    )
    assert observed["authorization"] == "Bearer vertex-access-token"
    receipt = response.receipt.model_dump_json()
    assert "vertex-access-token" not in receipt
    assert "configured-project" not in receipt
    assert "identity-project" not in receipt
    assert response.receipt.account_fingerprint == opaque_fingerprint(
        "configured-project"
    )
    assert response.receipt.region == "us-central1"


@pytest.mark.asyncio
async def test_vertex_adc_and_project_failures_are_secret_safe():
    secret = "adc-secret-path-or-token"

    async def broken_provider() -> ADCIdentity:
        raise RuntimeError(secret)

    adapter = VertexADCAdapter(environ={}, token_provider=broken_provider)
    with pytest.raises(ProviderConfigurationError) as caught:
        await adapter.complete(normalized_request())
    assert secret not in str(caught.value)
    assert caught.value.code == "adc_unavailable"


@pytest.mark.asyncio
async def test_default_adc_loader_suppresses_third_party_error(monkeypatch):
    secret = "private-adc-filename-and-token"

    def broken_default(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr("google.auth.default", broken_default)
    with pytest.raises(ProviderConfigurationError) as caught:
        await load_google_adc_identity()
    assert secret not in str(caught.value)
    assert caught.value.code == "adc_unavailable"


@pytest.mark.asyncio
async def test_default_adc_refresh_caps_google_request_timeout(monkeypatch):
    observed_timeouts: list[float] = []

    class FakeRequest:
        def __call__(self, *_args, timeout=None, **_kwargs):
            observed_timeouts.append(timeout)
            return object()

    class FakeCredentials:
        token = "adc-token"

        def refresh(self, request):
            request("https://metadata.invalid", timeout=99.0)

    monkeypatch.setattr(
        "google.auth.default",
        lambda **_kwargs: (FakeCredentials(), "valid-project"),
    )
    monkeypatch.setattr("google.auth.transport.requests.Request", FakeRequest)
    identity = await load_google_adc_identity(timeout_seconds=0.25)
    assert identity.project_id == "valid-project"
    assert observed_timeouts == [0.25]


@pytest.mark.asyncio
async def test_missing_key_and_http_error_bodies_never_escape():
    with pytest.raises(ProviderConfigurationError) as missing:
        await OpenAIAdapter(environ={}).complete(normalized_request())
    assert missing.value.code == "credential_missing"

    secret = "upstream-body-secret"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": secret}})

    client = async_client(handler)
    try:
        with pytest.raises(ProviderTransportError) as failed:
            await OpenAIAdapter(
                client=client, environ={"OPENAI_API_KEY": "key"}
            ).complete(normalized_request())
    finally:
        await client.aclose()
    assert secret not in str(failed.value)
    assert failed.value.code == "http_401"


@pytest.mark.asyncio
async def test_transport_exception_is_sanitized_and_redirect_is_not_followed():
    secret = "transport-secret"

    async def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(secret, request=request)

    client = async_client(broken)
    try:
        with pytest.raises(ProviderTransportError) as failed:
            await GeminiAdapter(
                client=client, environ={"GEMINI_API_KEY": "key"}
            ).complete(normalized_request())
    finally:
        await client.aclose()
    assert secret not in str(failed.value)
    assert failed.value.code == "transport_error"

    calls = 0

    async def redirect(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(307, headers={"Location": "https://attacker.invalid"})

    client = async_client(redirect)
    try:
        with pytest.raises(ProviderTransportError) as redirected:
            await AnthropicAdapter(
                client=client, environ={"ANTHROPIC_API_KEY": "key"}
            ).complete(normalized_request())
    finally:
        await client.aclose()
    assert redirected.value.code == "http_307"
    assert calls == 1


@pytest.mark.asyncio
async def test_response_size_and_shape_are_bounded():
    chunks_requested: list[int] = []

    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunks_requested.append(MAX_PROVIDER_RESPONSE_BYTES)
            yield b"x" * MAX_PROVIDER_RESPONSE_BYTES
            chunks_requested.append(1)
            yield b"y"
            raise AssertionError("adapter kept buffering after crossing the cap")

    async def huge(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OversizedStream())

    client = async_client(huge)
    try:
        with pytest.raises(ProviderProtocolError) as failed:
            await OpenAIAdapter(
                client=client, environ={"OPENAI_API_KEY": "key"}
            ).complete(normalized_request())
    finally:
        await client.aclose()
    assert failed.value.code == "response_too_large"
    assert chunks_requested == [MAX_PROVIDER_RESPONSE_BYTES, 1]


def test_registry_is_inert_complete_and_secret_free():
    secret_values = {
        "OPENAI_API_KEY": "openai-secret",
        "CLAUDE_API_KEY": "claude-secret",
        "GEMINI_API_KEY": "gemini-secret",
        "GOOGLE_APPLICATION_CREDENTIALS": "/private/adc-secret.json",
    }
    registry = build_provider_registry(environ=secret_values)
    assert set(registry) == {
        ProviderChannel.OPENAI_API,
        ProviderChannel.ANTHROPIC_API,
        ProviderChannel.GEMINI_API_KEY,
        ProviderChannel.VERTEX_ADC,
    }
    assert all(isinstance(adapter, ProviderAdapter) for adapter in registry.values())
    assert (
        registry[ProviderChannel.OPENAI_API].descriptor.credential_state
        == CredentialState.CONFIGURED
    )
    assert (
        registry[ProviderChannel.ANTHROPIC_API].descriptor.credential_state
        == CredentialState.CONFIGURED
    )
    assert (
        registry[ProviderChannel.GEMINI_API_KEY].descriptor.credential_state
        == CredentialState.CONFIGURED
    )
    assert (
        registry[ProviderChannel.VERTEX_ADC].descriptor.credential_state
        == CredentialState.DEFERRED
    )
    assert (
        registry[ProviderChannel.GEMINI_API_KEY].descriptor.provider
        == ProviderId.GOOGLE
    )
    assert registry[ProviderChannel.VERTEX_ADC].descriptor.provider == ProviderId.GOOGLE
    rendered = "\n".join(
        adapter.descriptor.model_dump_json() for adapter in registry.values()
    )
    for value in secret_values.values():
        assert value not in rendered


def test_current_stable_model_defaults_are_route_specific():
    registry = build_provider_registry(environ={})
    assert registry[ProviderChannel.OPENAI_API].descriptor.models.planning == "gpt-5.1"
    assert (
        registry[ProviderChannel.ANTHROPIC_API].descriptor.models.planning
        == "claude-fable-5"
    )
    assert (
        registry[ProviderChannel.ANTHROPIC_API].descriptor.models.coding
        == "claude-sonnet-5"
    )
    assert (
        registry[ProviderChannel.GEMINI_API_KEY].descriptor.models.coding
        == "gemini-3.5-flash"
    )
    assert (
        registry[ProviderChannel.VERTEX_ADC].descriptor.models.research
        == "gemini-3.5-flash"
    )
