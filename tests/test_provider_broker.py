from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import threading
from types import SimpleNamespace
from typing import Any
import time

import pytest
from pydantic import ValidationError

import src.providers.broker as provider_broker
from src.providers import (
    BrokerHarvestStatus,
    BrokerAttemptEvidence,
    BrokerCancellationPersistenceError,
    BrokerDelegationResult,
    CredentialPlane,
    CredentialState,
    GrokDelegationPlan,
    GrokSupervisorBinding,
    GrokWorkerBroker,
    GrokWorkerBrokerResult,
    GrokWorkerDelegation,
    GrokWorkerLaneAuthorization,
    ProviderAttemptStart,
    ProviderAttemptResult,
    ProviderChannel,
    ProviderDescriptor,
    ProviderFailureReceipt,
    ProviderId,
    ProviderMessage,
    ProviderModelPins,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    RouteClass,
    WorkerFallbackPolicy,
    transport_resource_identity,
)
from src.provider_harvest import ProviderAttemptHarvester
from src.providers.contracts import model_visible_messages
from src.utils import GrokSessionStore


NOW = datetime(2030, 1, 1, tzinfo=UTC)

_CHANNEL_INFO = {
    ProviderChannel.OPENAI_MCP_SAMPLING: (
        ProviderId.OPENAI,
        CredentialPlane.SUBSCRIPTION,
        "mcp-client",
        "mcp_client_sampling",
        "mcp_client_subscription",
        "subscription",
    ),
    ProviderChannel.OPENAI_API: (
        ProviderId.OPENAI,
        CredentialPlane.METERED_API,
        "api.openai.com",
        "first_party_api",
        "api_key",
        "metered",
    ),
    ProviderChannel.ANTHROPIC_MCP_SAMPLING: (
        ProviderId.ANTHROPIC,
        CredentialPlane.SUBSCRIPTION,
        "mcp-client",
        "mcp_client_sampling",
        "mcp_client_subscription",
        "subscription",
    ),
    ProviderChannel.CLAUDE_CLI: (
        ProviderId.ANTHROPIC,
        CredentialPlane.SUBSCRIPTION,
        "local-process",
        "local_cli",
        "host_oauth",
        "subscription",
    ),
    ProviderChannel.ANTHROPIC_API: (
        ProviderId.ANTHROPIC,
        CredentialPlane.METERED_API,
        "api.anthropic.com",
        "first_party_api",
        "api_key",
        "metered",
    ),
    ProviderChannel.GOOGLE_MCP_SAMPLING: (
        ProviderId.GOOGLE,
        CredentialPlane.SUBSCRIPTION,
        "mcp-client",
        "mcp_client_sampling",
        "mcp_client_subscription",
        "subscription",
    ),
    ProviderChannel.VERTEX_ADC: (
        ProviderId.GOOGLE,
        CredentialPlane.METERED_API,
        "aiplatform.googleapis.com",
        "vertex_ai",
        "google_adc",
        "metered",
    ),
    ProviderChannel.GEMINI_API_KEY: (
        ProviderId.GOOGLE,
        CredentialPlane.METERED_API,
        "generativelanguage.googleapis.com",
        "first_party_api",
        "api_key",
        "metered",
    ),
}


def _model(provider: ProviderId) -> str:
    return {
        ProviderId.OPENAI: "gpt-5.1",
        ProviderId.ANTHROPIC: "claude-fable-5",
        ProviderId.GOOGLE: "gemini-3.5-flash",
    }[provider]


def _descriptor(
    channel: ProviderChannel,
    *,
    state: CredentialState = CredentialState.CONFIGURED,
) -> ProviderDescriptor:
    provider, plane, host, endpoint, credential, billing = _CHANNEL_INFO[channel]
    model = _model(provider)
    return ProviderDescriptor(
        provider=provider,
        channel=channel,
        credential_plane=plane,
        display_name=f"test {channel.value}",
        endpoint_host=host,
        endpoint_kind=endpoint,
        credential_kind=credential,
        billing_class=billing,
        client_identity=(
            f"client-{provider.value}" if endpoint == "mcp_client_sampling" else None
        ),
        transport_resource_identity=transport_resource_identity(
            "test_worker_channel",
            channel.value,
        ),
        credential_env_names=(),
        credential_state=state,
        models=ProviderModelPins(
            planning=model,
            coding=model,
            vision=model,
            research=model,
        ),
        data_handling="provider_managed",
        residency="test",
    )


class FakeAdapter:
    def __init__(
        self,
        channel: ProviderChannel,
        *,
        outcome: str = "returned",
        failure_kind: str = "transport",
        delay: float = 0.0,
        events: list[str] | None = None,
        tracker: dict[str, int] | None = None,
        descriptor: ProviderDescriptor | None = None,
        response_text: str | None = None,
    ) -> None:
        self._descriptor = descriptor or _descriptor(channel)
        self.outcome = outcome
        self.failure_kind = failure_kind
        self.delay = delay
        self.events = events if events is not None else []
        self.tracker = tracker
        self.response_text = response_text
        self.calls = 0
        self.requests: list[Any] = []

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def complete(self, request):
        result = await self.attempt(request)
        if result.response is None:
            raise RuntimeError("fake failure")
        return result.response

    async def attempt(self, request) -> ProviderAttemptResult:
        self.calls += 1
        self.requests.append(request)
        self.events.append(f"effect:{self.descriptor.channel.value}")
        if self.tracker is not None:
            self.tracker["active"] += 1
            self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.outcome == "raise":
                raise RuntimeError("secret-bearing provider failure must not escape")
            if self.outcome == "spoof":
                spoof = _descriptor(ProviderChannel.ANTHROPIC_API)
                receipt = _receipt(request, spoof)
                return ProviderAttemptResult(
                    status="returned",
                    response=ProviderResponse(
                        provider=spoof.provider,
                        channel=spoof.channel,
                        model=receipt.resolved_model,
                        text="spoofed worker output",
                        finish_reason="stop",
                        receipt=receipt,
                    ),
                )
            receipt = _receipt(request, self.descriptor)
            if self.outcome == "failed":
                return ProviderAttemptResult(
                    status="failed",
                    failure=ProviderFailureReceipt(
                        request_id=request.request_id,
                        supervision=request.supervision,
                        provider=self.descriptor.provider,
                        channel=self.descriptor.channel,
                        credential_plane=self.descriptor.credential_plane,
                        route=request.route,
                        requested_model=request.model,
                        endpoint_host=self.descriptor.endpoint_host,
                        endpoint_kind=self.descriptor.endpoint_kind,
                        credential_kind=self.descriptor.credential_kind,
                        billing_class=self.descriptor.billing_class,
                        client_identity=self.descriptor.client_identity,
                        error_kind=self.failure_kind,
                        error_code="test_failure",
                        duration_ms=2,
                    ),
                )
            return ProviderAttemptResult(
                status="returned",
                response=ProviderResponse(
                    provider=self.descriptor.provider,
                    channel=self.descriptor.channel,
                    model=receipt.resolved_model,
                    text=(
                        self.response_text
                        if self.response_text is not None
                        else f"evidence from {self.descriptor.channel.value}"
                    ),
                    finish_reason="stop",
                    receipt=receipt,
                ),
            )
        finally:
            if self.tracker is not None:
                self.tracker["active"] -= 1


def _receipt(request, descriptor: ProviderDescriptor) -> ProviderReceipt:
    return ProviderReceipt(
        request_id=request.request_id,
        supervision=request.supervision,
        provider=descriptor.provider,
        channel=descriptor.channel,
        credential_plane=descriptor.credential_plane,
        route=request.route,
        requested_model=request.model,
        resolved_model=request.model,
        model_source="provider_reported",
        endpoint_host=descriptor.endpoint_host,
        endpoint_kind=descriptor.endpoint_kind,
        credential_kind=descriptor.credential_kind,
        billing_class=descriptor.billing_class,
        client_identity=descriptor.client_identity,
        region=descriptor.residency,
        duration_ms=2,
        usage=ProviderTokenUsage(),
    )


class FakeStore:
    def __init__(
        self,
        *,
        events: list[str] | None = None,
        fail_begin: bool = False,
        fail_complete: bool = False,
    ) -> None:
        self.events = events if events is not None else []
        self.fail_begin = fail_begin
        self.fail_complete = fail_complete
        self.starts: dict[str, Any] = {}
        self.results: dict[str, ProviderAttemptResult] = {}

    async def begin_provider_attempt(self, start) -> bool:
        self.events.append(f"begin:{start.channel.value}")
        if self.fail_begin:
            raise RuntimeError("begin unavailable")
        existing = self.starts.get(start.attempt_id)
        if existing is not None:
            if existing != start:
                raise ValueError("identity conflict")
            return False
        if any(
            prior.delegation_id == start.delegation_id
            and prior.attempt_ordinal == start.attempt_ordinal
            for prior in self.starts.values()
        ):
            raise ValueError("delegation ordinal identity conflict")
        self.starts[start.attempt_id] = start
        return True

    async def complete_provider_attempt(self, attempt_id, result) -> bool:
        self.events.append(f"complete:{self.starts[attempt_id].channel.value}")
        if self.fail_complete:
            raise RuntimeError("terminal unavailable")
        existing = self.results.get(attempt_id)
        if existing is not None:
            if existing != result:
                raise ValueError("terminal conflict")
            return False
        self.results[attempt_id] = result
        return True

    async def list_provider_attempts(
        self,
        supervisor_session_id=None,
        delegation_id=None,
        limit=100,
    ):
        rows = []
        for attempt_id, start in self.starts.items():
            if supervisor_session_id not in (
                None,
                start.request.supervision.session_id,
            ):
                continue
            if delegation_id not in (None, start.delegation_id):
                continue
            visible = [
                message.model_dump(mode="json")
                for message in model_visible_messages(start.request)
            ]
            prompt_text = json.dumps(
                visible,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            prompt_digest = (
                "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            )
            start_record = start.model_dump(mode="json")
            start_record["request"].pop("messages")
            start_record.update(
                {
                    "model_visible_prompt_digest": prompt_digest,
                    "prompt_redaction": "clean",
                    "started_at": NOW.isoformat(),
                }
            )
            start_json = json.dumps(
                start_record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            base_row = {
                "attempt_id": attempt_id,
                "delegation_id": start.delegation_id,
                "attempt_ordinal": start.attempt_ordinal,
                "start_json": start_json,
                "start_digest": (
                    "sha256:" + hashlib.sha256(start_json.encode("utf-8")).hexdigest()
                ),
                "supervisor_plane": start.supervisor_plane,
                "supervisor_model": start.supervisor_model,
                "supervisor_session_id": start.request.supervision.session_id,
                "objective_id": start.request.supervision.objective_id,
                "route_decision_id": start.request.supervision.route_decision_id,
                "ttl_expires_at": start.request.supervision.ttl_expires_at.isoformat(),
                "request_id": start.request.request_id,
                "provider": start.provider.value,
                "channel": start.channel.value,
                "credential_plane": start.credential_plane.value,
                "route": start.request.route.value,
                "requested_model": start.requested_model,
                "prompt_text": prompt_text,
                "prompt_digest": prompt_digest,
            }
            result = self.results.get(attempt_id)
            if result is None:
                rows.append({**base_row, "transport_status": "started"})
                continue
            receipt = result.response.receipt if result.response else result.failure
            rows.append(
                {
                    **base_row,
                    "transport_status": result.status,
                    "receipt": receipt.model_dump(mode="json"),
                    "resolved_model": (
                        result.response.model if result.response is not None else None
                    ),
                    "output_text": (
                        result.response.text if result.response is not None else None
                    ),
                    "finish_reason": (
                        result.response.finish_reason
                        if result.response is not None
                        else None
                    ),
                }
            )
        return rows[:limit]


class BlockingCompleteStore(FakeStore):
    def __init__(self, *, fail_after_release: bool = False) -> None:
        super().__init__()
        self.fail_after_release = fail_after_release
        self.complete_entered = asyncio.Event()
        self.complete_release = asyncio.Event()

    async def complete_provider_attempt(self, attempt_id, result) -> bool:
        self.events.append(f"complete:{self.starts[attempt_id].channel.value}")
        self.complete_entered.set()
        await self.complete_release.wait()
        if self.fail_after_release:
            raise RuntimeError("terminal write failed after release")
        existing = self.results.get(attempt_id)
        if existing is not None:
            if existing != result:
                raise ValueError("terminal conflict")
            return False
        self.results[attempt_id] = result
        return True


class FakeHarvester:
    def __init__(self, *, outcome: str = "complete", delay: float = 0.0) -> None:
        self.outcome = outcome
        self.delay = delay
        self.calls = 0
        self.deadlines: list[float] = []

    async def run_once(self, store, *, deadline_monotonic: float):
        self.calls += 1
        self.deadlines.append(deadline_monotonic)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.outcome == "raise":
            raise RuntimeError("cloud secret must not escape")
        return SimpleNamespace(
            status=self.outcome,
            reason=(
                "management_key_missing" if self.outcome == "unavailable" else None
            ),
            leased=1 if self.outcome in {"complete", "partial"} else 0,
            synced=1 if self.outcome == "complete" else 0,
            retry_wait=1 if self.outcome == "partial" else 0,
            lease_lost=0,
            state_errors=0,
        )


def _fallback(enabled: bool = True) -> WorkerFallbackPolicy:
    return WorkerFallbackPolicy(
        mode="subscription_then_api" if enabled else "subscription_only",
        max_metered_api_attempts=1 if enabled else 0,
    )


def _plan(
    provider: ProviderId = ProviderId.OPENAI,
    *,
    count: int = 1,
    fallback: bool = True,
    ttl: datetime | None = None,
    max_concurrency: int = 4,
    timeout_seconds: float = 60.0,
    lane_descriptors: tuple[ProviderDescriptor, ...] | None = None,
) -> GrokDelegationPlan:
    if lane_descriptors is None:
        lane_descriptors = tuple(
            _descriptor(channel)
            for channel, (lane_provider, plane, *_rest) in _CHANNEL_INFO.items()
            if lane_provider == provider
            and (plane == CredentialPlane.SUBSCRIPTION or fallback)
        )
    authorized_lanes = tuple(
        GrokWorkerLaneAuthorization.from_descriptor(descriptor)
        for descriptor in lane_descriptors
    )
    return GrokDelegationPlan(
        supervision=GrokSupervisorBinding(
            session_id="session-1",
            objective_id="objective-1",
            route_decision_id="route-1",
            ttl_expires_at=ttl or NOW + timedelta(minutes=5),
        ),
        supervisor_plane="CLI",
        supervisor_model="grok-4.5",
        delegations=tuple(
            GrokWorkerDelegation(
                delegation_key=f"work-{index}",
                provider=provider,
                route=RouteClass.PLANNING,
                messages=(
                    ProviderMessage(
                        role="user",
                        content=f"Return bounded evidence for item {index}.",
                    ),
                ),
                fallback=_fallback(fallback),
                authorized_lanes=authorized_lanes,
                timeout_seconds=timeout_seconds,
            )
            for index in range(count)
        ),
        max_concurrency=max_concurrency,
    )


def _forged_attempt(
    plan: GrokDelegationPlan,
    *,
    base_descriptor: ProviderDescriptor,
    forged_descriptor: ProviderDescriptor,
    ordinal: int,
) -> tuple[ProviderAttemptStart, ProviderAttemptResult]:
    """Build self-consistent attacker evidence with every digest recomputed."""

    delegation = plan.delegations[0]
    delegation_id = provider_broker._delegation_id(plan, 0)
    execution = provider_broker._execution_binding(forged_descriptor)
    requested_model = execution.model_for_route(delegation.route)
    max_output_tokens = min(
        delegation.max_output_tokens,
        execution.max_output_tokens,
    )
    timeout_seconds = min(
        delegation.timeout_seconds,
        execution.max_timeout_seconds,
    )
    execution_digest = provider_broker._execution_contract_digest(
        provider=forged_descriptor.provider,
        channel=forged_descriptor.channel,
        credential_plane=forged_descriptor.credential_plane,
        execution=execution,
        route=delegation.route,
        requested_model=requested_model,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
    )
    attempt_id = provider_broker._stable_id(
        "att",
        plan.plan_digest,
        delegation_id,
        forged_descriptor.channel.value,
        str(ordinal),
        requested_model,
        execution_digest,
    )
    request_id = provider_broker._stable_id(
        "req",
        plan.plan_digest,
        delegation_id,
        attempt_id,
        forged_descriptor.channel.value,
        requested_model,
        execution_digest,
    )
    request = ProviderRequest(
        request_id=request_id,
        supervision=plan.supervision,
        route=delegation.route,
        messages=delegation.messages,
        model=requested_model,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        temperature=delegation.temperature,
    )
    start = ProviderAttemptStart(
        version="provider-attempt-start/v3",
        attempt_id=attempt_id,
        delegation_id=delegation_id,
        attempt_ordinal=ordinal,
        supervisor_plane=plan.supervisor_plane,
        supervisor_model=plan.supervisor_model,
        provider=forged_descriptor.provider,
        channel=forged_descriptor.channel,
        credential_plane=forged_descriptor.credential_plane,
        requested_model=requested_model,
        execution=execution,
        request=request,
    )
    receipt = _receipt(request, forged_descriptor)
    result = ProviderAttemptResult(
        status="returned",
        response=ProviderResponse(
            provider=forged_descriptor.provider,
            channel=forged_descriptor.channel,
            model=receipt.resolved_model,
            text="forged but internally self-consistent worker evidence",
            finish_reason="stop",
            receipt=receipt,
        ),
    )
    assert base_descriptor.provider == forged_descriptor.provider
    return start, result


@pytest.mark.asyncio
async def test_plan_is_strict_content_addressed_and_construction_is_inert():
    plan = _plan()
    same = _plan()
    changed = _plan(provider=ProviderId.ANTHROPIC)
    assert plan.plan_id == same.plan_id
    assert plan.plan_digest == same.plan_digest
    assert plan.plan_digest != changed.plan_digest
    aliased_mapping = {
        "version": plan.version,
        "supervision": plan.supervision,
        "supervisor_plane": plan.supervisor_plane,
        "supervisor_model": plan.supervisor_model,
        "delegations": plan.delegations,
        "max_concurrency": plan.max_concurrency,
    }
    for surface in (plan, aliased_mapping):
        snapshot = provider_broker._snapshot_plan(surface)
        assert snapshot.supervision is not plan.supervision
        assert snapshot.delegations[0] is not plan.delegations[0]
        assert (
            snapshot.delegations[0].messages[0] is not (plan.delegations[0].messages[0])
        )
        assert (
            snapshot.delegations[0].authorized_lanes[0]
            is not (plan.delegations[0].authorized_lanes[0])
        )
    base_api = _descriptor(ProviderChannel.OPENAI_API)
    rotated_api = base_api.model_copy(update={"endpoint_host": "rotated.openai.com"})
    assert (
        _plan(lane_descriptors=(base_api,)).plan_digest
        != _plan(lane_descriptors=(rotated_api,)).plan_digest
    )

    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    store = FakeStore()
    harvester = FakeHarvester()
    GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        harvester=harvester,
        clock=lambda: NOW,
    )
    assert adapter.calls == 0
    assert not store.starts
    assert harvester.calls == 0

    raw = plan.model_dump(mode="python")
    raw["authority"] = {"may_finalize": True}
    with pytest.raises(ValidationError, match="Extra inputs"):
        GrokDelegationPlan.model_validate(raw)

    with pytest.raises(ValidationError, match="combined delegation content"):
        GrokWorkerDelegation(
            delegation_key="oversized",
            provider=ProviderId.OPENAI,
            route=RouteClass.PLANNING,
            messages=tuple(
                ProviderMessage(role="user", content="x" * 128_000) for _ in range(4)
            ),
            fallback=_fallback(),
            authorized_lanes=_plan().delegations[0].authorized_lanes,
        )

    raw = plan.model_dump(mode="python")
    raw["delegations"][0]["channel"] = "openai_api"
    with pytest.raises(ValidationError, match="Extra inputs"):
        GrokDelegationPlan.model_validate(raw)

    raw = plan.model_dump(mode="python")
    raw["delegations"][0]["authorized_lanes"] = tuple(
        reversed(raw["delegations"][0]["authorized_lanes"])
    )
    with pytest.raises(ValidationError, match="fixed provider ladder"):
        GrokDelegationPlan.model_validate(raw)


@pytest.mark.asyncio
async def test_mapping_plan_alias_mutation_cannot_change_in_flight_execution():
    descriptor = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    plan = _plan(fallback=False, lane_descriptors=(descriptor,))
    original_digest = plan.plan_digest
    original_prompt = plan.delegations[0].messages[0].content
    aliased_mapping = {
        "version": plan.version,
        "supervision": plan.supervision,
        "supervisor_plane": plan.supervisor_plane,
        "supervisor_model": plan.supervisor_model,
        "delegations": plan.delegations,
        "max_concurrency": plan.max_concurrency,
    }

    class MutatingPlanAdapter(FakeAdapter):
        async def attempt(self, request):
            object.__setattr__(
                plan.delegations[0].messages[0],
                "content",
                "forged caller-side plan mutation",
            )
            return await super().attempt(request)

    adapter = MutatingPlanAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        descriptor=descriptor,
    )
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(aliased_mapping)

    assert result.status == "returned"
    assert result.plan_digest == original_digest
    assert adapter.requests[0].messages[0].content == original_prompt
    assert plan.delegations[0].messages[0].content != original_prompt


@pytest.mark.asyncio
async def test_subscription_success_prevents_api_and_is_durable_before_harvest():
    events: list[str] = []
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        events=events,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API, events=events)
    store = FakeStore(events=events)
    harvester = FakeHarvester()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        harvester=harvester,
        clock=lambda: NOW,
    ).execute(_plan())

    assert result.status == "returned"
    assert subscription.calls == 1
    assert api.calls == 0
    assert events == [
        "begin:openai_mcp_sampling",
        "effect:openai_mcp_sampling",
        "complete:openai_mcp_sampling",
    ]
    evidence = result.delegations[0].attempts[0]
    assert evidence.persistence == "durable_terminal"
    assert evidence.harvest.status == "complete"
    assert harvester.calls == 1
    assert result.synthesized is False
    assert result.authority.may_finalize is False
    assert evidence.authority.may_route is False


@pytest.mark.asyncio
async def test_subscription_failure_uses_one_same_provider_api_fallback_only():
    subscription = FakeAdapter(
        ProviderChannel.ANTHROPIC_MCP_SAMPLING,
        outcome="failed",
        failure_kind="configuration",
    )
    cli = FakeAdapter(ProviderChannel.CLAUDE_CLI)
    api = FakeAdapter(ProviderChannel.ANTHROPIC_API)
    unrelated = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.ANTHROPIC_MCP_SAMPLING: subscription,
            ProviderChannel.CLAUDE_CLI: cli,
            ProviderChannel.ANTHROPIC_API: api,
            ProviderChannel.OPENAI_API: unrelated,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(ProviderId.ANTHROPIC))

    assert result.status == "returned"
    assert subscription.calls == 1
    assert cli.calls == 0
    assert api.calls == 1
    assert unrelated.calls == 0
    attempts = result.delegations[0].attempts
    assert [attempt.start.attempt_ordinal for attempt in attempts] == [1, 2]
    assert [attempt.start.provider for attempt in attempts] == [
        ProviderId.ANTHROPIC,
        ProviderId.ANTHROPIC,
    ]
    assert all(attempt.harvest.status == "unavailable" for attempt in attempts)


@pytest.mark.asyncio
async def test_internal_subscription_failure_does_not_fallback_or_cross_provider():
    subscription = FakeAdapter(
        ProviderChannel.GOOGLE_MCP_SAMPLING,
        outcome="failed",
        failure_kind="internal",
    )
    vertex = FakeAdapter(ProviderChannel.VERTEX_ADC)
    openai = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.GOOGLE_MCP_SAMPLING: subscription,
            ProviderChannel.VERTEX_ADC: vertex,
            ProviderChannel.OPENAI_API: openai,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(ProviderId.GOOGLE))

    assert result.status == "failed"
    assert subscription.calls == 1
    assert vertex.calls == 0
    assert openai.calls == 0


@pytest.mark.asyncio
async def test_google_uses_vertex_or_gemini_but_never_two_metered_attempts():
    vertex = FakeAdapter(ProviderChannel.VERTEX_ADC, outcome="failed")
    gemini = FakeAdapter(ProviderChannel.GEMINI_API_KEY)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.VERTEX_ADC: vertex,
            ProviderChannel.GEMINI_API_KEY: gemini,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(ProviderId.GOOGLE))
    assert result.status == "failed"
    assert vertex.calls == 1
    assert gemini.calls == 0
    assert len(result.delegations[0].attempts) == 1
    assert result.delegations[0].attempts[0].start.attempt_ordinal == 2

    missing_vertex = FakeAdapter(
        ProviderChannel.VERTEX_ADC,
        descriptor=_descriptor(
            ProviderChannel.VERTEX_ADC,
            state=CredentialState.MISSING,
        ),
    )
    gemini = FakeAdapter(ProviderChannel.GEMINI_API_KEY)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.VERTEX_ADC: missing_vertex,
            ProviderChannel.GEMINI_API_KEY: gemini,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(ProviderId.GOOGLE))
    assert result.status == "returned"
    assert missing_vertex.calls == 0
    assert gemini.calls == 1


@pytest.mark.asyncio
async def test_begin_failure_has_zero_provider_effect_and_no_harvest():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    harvester = FakeHarvester()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(fail_begin=True),
        harvester=harvester,
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))

    assert result.status == "indeterminate"
    assert adapter.calls == 0
    assert harvester.calls == 0
    evidence = result.delegations[0].attempts[0]
    assert evidence.persistence == "begin_failed"
    assert evidence.result is None


@pytest.mark.asyncio
async def test_terminal_store_failure_is_indeterminate_and_withholds_worker_output():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    harvester = FakeHarvester()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(fail_complete=True),
        harvester=harvester,
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))

    evidence = result.delegations[0].attempts[0]
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.status == "not_applicable"
    assert harvester.calls == 0


@pytest.mark.asyncio
async def test_adapter_exception_and_spoof_are_normalized_and_recorded():
    for outcome, expected_kind, expected_code in (
        ("raise", "internal", "unexpected_adapter_exception"),
        ("spoof", "protocol", "adapter_contract_mismatch"),
    ):
        store = FakeStore()
        adapter = FakeAdapter(
            ProviderChannel.OPENAI_MCP_SAMPLING,
            outcome=outcome,
        )
        result = await GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
        evidence = result.delegations[0].attempts[0]
        assert evidence.persistence == "durable_terminal"
        assert evidence.result.status == "failed"
        assert evidence.result.failure.error_kind == expected_kind
        assert evidence.result.failure.error_code == expected_code
        assert store.results[evidence.start.attempt_id] == evidence.result


@pytest.mark.asyncio
async def test_exact_replay_reuses_content_bound_ids_without_second_effect():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    store = FakeStore()
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)
    first = await broker.execute(plan)
    second = await broker.execute(plan)

    assert adapter.calls == 1
    first_attempt = first.delegations[0].attempts[0]
    second_attempt = second.delegations[0].attempts[0]
    assert first_attempt.start == second_attempt.start
    assert first_attempt.start.request.request_id.startswith("req:")
    assert first_attempt.start.attempt_id.startswith("att:")
    assert first_attempt.start.delegation_id.startswith("dlg:")
    assert second_attempt.persistence == "replayed_terminal"
    assert second_attempt.result == first_attempt.result


@pytest.mark.asyncio
async def test_real_provider_ledger_replays_without_duplicate_effect(tmp_path):
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    store = GrokSessionStore(tmp_path / "broker-ledger.db")
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)
    first = await broker.execute(plan)
    second = await broker.execute(plan)
    rows = await store.list_provider_attempts(
        delegation_id=first.delegations[0].delegation_id
    )
    assert adapter.calls == 1
    assert first.status == second.status == "returned"
    assert second.delegations[0].attempts[0].persistence == "replayed_terminal"
    assert len(rows) == 1
    assert rows[0]["transport_status"] == "returned"
    await store.close()


@pytest.mark.asyncio
async def test_first_return_uses_same_redacted_durable_result_as_replay(
    tmp_path,
    monkeypatch,
):
    secret = "sk-ant-worker-secret-that-must-never-be-returned"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        response_text=f"Worker echoed {secret}",
    )
    store = GrokSessionStore(tmp_path / "broker-redacted-return.db")
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)

    first = await broker.execute(plan)
    replay = await broker.execute(plan)
    first_attempt = first.delegations[0].attempts[0]
    replay_attempt = replay.delegations[0].attempts[0]

    assert adapter.calls == 1
    assert first_attempt.result == replay_attempt.result
    assert first_attempt.start == replay_attempt.start
    assert secret not in first_attempt.result.response.text
    assert secret not in replay_attempt.result.response.text
    assert first_attempt.persistence == "durable_terminal"
    assert replay_attempt.persistence == "replayed_terminal"
    await store.close()


@pytest.mark.asyncio
async def test_adapter_cannot_mutate_frozen_prompt_after_durable_begin(tmp_path):
    class MutatingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(ProviderChannel.OPENAI_MCP_SAMPLING)
            self.mutation_errors: list[BaseException] = []
            self.physical_messages = None

        async def attempt(self, request):
            try:
                request.messages.append(
                    ProviderMessage(role="user", content="forged late prompt")
                )
            except BaseException as exc:
                self.mutation_errors.append(exc)
            try:
                request.messages[0].content = "forged nested mutation"
            except BaseException as exc:
                self.mutation_errors.append(exc)
            self.physical_messages = request.messages
            return await super().attempt(request)

    adapter = MutatingAdapter()
    store = GrokSessionStore(tmp_path / "broker-frozen-prompt.db")
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)
    first = await broker.execute(plan)
    replay = await broker.execute(plan)

    assert adapter.calls == 1
    assert len(adapter.mutation_errors) == 2
    assert adapter.physical_messages == plan.delegations[0].messages
    assert first.delegations[0].attempts[0].start == (
        replay.delegations[0].attempts[0].start
    )
    rows = await store.list_provider_attempts()
    reconstructed = broker._reconstruct_stored_start(rows[0])
    assert reconstructed.request.messages == adapter.physical_messages
    await store.close()


@pytest.mark.asyncio
async def test_effect_edge_rejects_forced_prompt_mutation_without_changing_ledger(
    tmp_path,
):
    class HostileAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(ProviderChannel.OPENAI_MCP_SAMPLING)
            self.physical_messages = None

        async def attempt(self, request):
            object.__setattr__(
                request.messages[0],
                "content",
                "forged physical prompt after durable begin",
            )
            self.physical_messages = request.messages
            return await super().attempt(request)

    adapter = HostileAdapter()
    store = GrokSessionStore(tmp_path / "broker-hostile-prompt.db")
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)

    first = await broker.execute(plan)
    replay = await broker.execute(plan)
    first_attempt = first.delegations[0].attempts[0]
    replay_attempt = replay.delegations[0].attempts[0]

    assert adapter.calls == 1
    assert adapter.physical_messages != plan.delegations[0].messages
    assert first.status == "failed"
    assert first_attempt.result.failure.error_code == "adapter_request_mutation"
    assert first_attempt.start.request.messages == plan.delegations[0].messages
    assert first_attempt.result == replay_attempt.result
    assert replay_attempt.start == first_attempt.start
    rows = await store.list_provider_attempts()
    reconstructed = broker._reconstruct_stored_start(rows[0])
    assert reconstructed.request.messages == plan.delegations[0].messages
    await store.close()


@pytest.mark.asyncio
async def test_durable_replay_ignores_current_descriptor_and_lane_availability():
    base_descriptor = _descriptor(ProviderChannel.OPENAI_API)
    first_adapter = FakeAdapter(
        ProviderChannel.OPENAI_API,
        descriptor=base_descriptor,
    )
    store = FakeStore()
    plan = _plan()
    first = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: first_adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    metadata_only = base_descriptor.model_copy(
        update={
            "display_name": "renamed subscription lane",
            "credential_state": CredentialState.DEFERRED,
            "credential_env_names": ("OPENAI_SECONDARY_KEY",),
        }
    )
    metadata_adapter = FakeAdapter(
        ProviderChannel.OPENAI_API,
        descriptor=metadata_only,
    )
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: metadata_adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert first.status == replay.status == "returned"
    assert metadata_adapter.calls == 0
    assert replay.delegations[0].attempts[0].persistence == "replayed_terminal"

    changed_endpoint = base_descriptor.model_copy(
        update={"endpoint_host": "alternate.openai.com"}
    )
    changed_adapter = FakeAdapter(
        ProviderChannel.OPENAI_API,
        descriptor=changed_endpoint,
    )
    endpoint_replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: changed_adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert endpoint_replay.status == "returned"
    assert changed_adapter.calls == 0

    removed_lane_replay = await GrokWorkerBroker(
        registry={},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert removed_lane_replay.status == "returned"
    assert removed_lane_replay.delegations[0].attempts[0].result == (
        first.delegations[0].attempts[0].result
    )


@pytest.mark.asyncio
async def test_exact_delegation_replays_api_when_subscription_later_appears(tmp_path):
    store = GrokSessionStore(tmp_path / "availability-drift-replay.db")
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    plan = _plan()
    first = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: api},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    subscription = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    replay = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert first.status == replay.status == "returned"
    assert api.calls == 1
    assert subscription.calls == 0
    assert [item.start.attempt_ordinal for item in replay.delegations[0].attempts] == [
        2
    ]
    rows = await store.list_provider_attempts(
        delegation_id=replay.delegations[0].delegation_id
    )
    assert len(rows) == 1
    await store.close()


@pytest.mark.asyncio
async def test_exact_delegation_replays_api_when_missing_subscription_appears():
    missing = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        descriptor=_descriptor(
            ProviderChannel.OPENAI_MCP_SAMPLING,
            state=CredentialState.MISSING,
        ),
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    plan = _plan()
    first = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: missing,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    available = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: available},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert first.status == replay.status == "returned"
    assert missing.calls == available.calls == 0
    assert api.calls == 1


@pytest.mark.asyncio
async def test_exact_failed_fallback_sequence_replays_without_new_effect():
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome="failed",
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API, outcome="failed")
    store = FakeStore()
    plan = _plan()
    first = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    replay = await GrokWorkerBroker(
        registry={},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert first.status == replay.status == "failed"
    assert subscription.calls == api.calls == 1
    assert [item.persistence for item in replay.delegations[0].attempts] == [
        "replayed_terminal",
        "replayed_terminal",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_status", ["started", "indeterminate"])
async def test_existing_nonterminal_delegation_blocks_every_new_lane(stored_status):
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    plan = _plan()
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: api},
        store=store,
        clock=lambda: NOW,
    )
    start = broker._start(
        plan=plan,
        index=0,
        channel=ProviderChannel.OPENAI_API,
        descriptor=api.descriptor,
        ordinal=2,
    )
    await store.begin_provider_attempt(start)

    original_list = store.list_provider_attempts

    async def projected_list(*args, **kwargs):
        rows = await original_list(*args, **kwargs)
        rows[0]["transport_status"] = stored_status
        return rows

    store.list_provider_attempts = projected_list
    subscription = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert result.status == "indeterminate"
    assert subscription.calls == api.calls == 0
    assert result.delegations[0].attempts[0].persistence == "replay_indeterminate"


@pytest.mark.asyncio
async def test_conflicting_stored_identity_blocks_new_effect():
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    plan = _plan()
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: api},
        store=store,
        clock=lambda: NOW,
    )
    first = await broker.execute(plan)
    assert first.status == "returned"

    original_list = store.list_provider_attempts

    async def conflicting_list(*args, **kwargs):
        rows = await original_list(*args, **kwargs)
        rows[0]["request_id"] = "req:" + ("f" * 64)
        return rows

    store.list_provider_attempts = conflicting_list
    subscription = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    refused = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert refused.status == "indeterminate"
    assert refused.delegations[0].reason == "stored_attempt_conflict"
    assert subscription.calls == 0
    assert api.calls == 1


@pytest.mark.asyncio
async def test_forged_content_addressed_attempt_and_request_ids_cannot_replay():
    adapter = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    plan = _plan()
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: adapter},
        store=store,
        clock=lambda: NOW,
    )
    first = await broker.execute(plan)
    original_start = next(iter(store.starts.values()))
    original_result = next(iter(store.results.values()))
    forged_request = original_start.request.model_copy(
        update={"request_id": "req:" + ("f" * 64)}
    )
    forged_start = original_start.model_copy(
        update={
            "attempt_id": "att:" + ("f" * 64),
            "request": forged_request,
        }
    )
    receipt = original_result.response.receipt.model_copy(
        update={"request_id": forged_request.request_id}
    )
    forged_response = original_result.response.model_copy(update={"receipt": receipt})
    forged_result = original_result.model_copy(update={"response": forged_response})
    store.starts = {forged_start.attempt_id: forged_start}
    store.results = {forged_start.attempt_id: forged_result}

    refused = await broker.execute(plan)
    assert first.status == "returned"
    assert refused.status == "indeterminate"
    assert refused.delegations[0].reason == "stored_attempt_conflict"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_plan_lane_digest_rejects_recomputed_model_caps_and_route_forgery():
    base = _descriptor(ProviderChannel.OPENAI_API)
    forged = base.model_copy(
        update={
            "models": base.models.model_copy(update={"planning": "gpt-forged"}),
            "supported_routes": (RouteClass.PLANNING,),
            "max_output_tokens": 512,
            "max_timeout_seconds": 7.0,
        }
    )
    plan = _plan(lane_descriptors=(base,))
    start, forged_result = _forged_attempt(
        plan,
        base_descriptor=base,
        forged_descriptor=forged,
        ordinal=2,
    )
    evidence = BrokerAttemptEvidence(
        start=start,
        persistence="replayed_terminal",
        result=forged_result,
        harvest=BrokerHarvestStatus(
            status="not_applicable",
            reason="delegation_replayed",
        ),
    )
    exported = GrokWorkerBrokerResult(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        supervision=plan.supervision,
        supervisor_plane=plan.supervisor_plane,
        supervisor_model=plan.supervisor_model,
        status="returned",
        delegations=(
            BrokerDelegationResult(
                delegation_id=start.delegation_id,
                delegation_key=plan.delegations[0].delegation_key,
                provider=plan.delegations[0].provider,
                route=plan.delegations[0].route,
                status="returned",
                reason="same_provider_api_returned",
                attempts=(evidence,),
            ),
        ),
    )
    with pytest.raises(ValueError, match="plan delegation"):
        exported.validate_against_plan(plan)

    store = FakeStore()
    assert await store.begin_provider_attempt(start)
    assert await store.complete_provider_attempt(start.attempt_id, forged_result)
    adapter = FakeAdapter(ProviderChannel.OPENAI_API, descriptor=base)
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert replay.status == "indeterminate"
    assert replay.delegations[0].reason == "stored_attempt_conflict"
    assert replay.delegations[0].attempts == ()
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_real_ledger_rejects_recomputed_endpoint_residency_policy_forgery(
    tmp_path,
):
    base = _descriptor(ProviderChannel.OPENAI_API)
    forged = base.model_copy(
        update={
            "endpoint_host": "alternate.openai.com",
            "data_handling": "project_policy",
            "residency": "forged-region",
            "supports_normalized_tools": True,
        }
    )
    plan = _plan(lane_descriptors=(base,))
    start, forged_result = _forged_attempt(
        plan,
        base_descriptor=base,
        forged_descriptor=forged,
        ordinal=2,
    )
    store = GrokSessionStore(tmp_path / "forged-lane-replay.db")
    assert await store.begin_provider_attempt(start)
    assert await store.complete_provider_attempt(start.attempt_id, forged_result)
    assert (await store.list_provider_attempts())[0]["transport_status"] == "returned"

    adapter = FakeAdapter(ProviderChannel.OPENAI_API, descriptor=base)
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert replay.status == "indeterminate"
    assert replay.delegations[0].reason == "stored_attempt_conflict"
    assert replay.delegations[0].attempts == ()
    assert adapter.calls == 0
    assert "forged but internally" not in replay.model_dump_json()
    await store.close()


@pytest.mark.asyncio
async def test_plan_rejects_recomputed_unauthorized_alternate_channel():
    authorized = _descriptor(ProviderChannel.ANTHROPIC_MCP_SAMPLING)
    alternate = _descriptor(ProviderChannel.CLAUDE_CLI)
    plan = _plan(
        ProviderId.ANTHROPIC,
        fallback=False,
        lane_descriptors=(authorized,),
    )
    start, forged_result = _forged_attempt(
        plan,
        base_descriptor=authorized,
        forged_descriptor=alternate,
        ordinal=1,
    )
    store = FakeStore()
    assert await store.begin_provider_attempt(start)
    assert await store.complete_provider_attempt(start.attempt_id, forged_result)
    adapter = FakeAdapter(
        ProviderChannel.ANTHROPIC_MCP_SAMPLING,
        descriptor=authorized,
    )

    replay = await GrokWorkerBroker(
        registry={ProviderChannel.ANTHROPIC_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert replay.status == "indeterminate"
    assert replay.delegations[0].reason == "stored_attempt_conflict"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_replay_rejects_api_fallback_after_internal_subscription_failure():
    subscription = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    api = _descriptor(ProviderChannel.OPENAI_API)
    plan = _plan(lane_descriptors=(subscription, api))
    subscription_start, _ = _forged_attempt(
        plan,
        base_descriptor=subscription,
        forged_descriptor=subscription,
        ordinal=1,
    )
    internal_failure = ProviderAttemptResult(
        status="failed",
        failure=ProviderFailureReceipt(
            request_id=subscription_start.request.request_id,
            supervision=subscription_start.request.supervision,
            provider=subscription.provider,
            channel=subscription.channel,
            credential_plane=subscription.credential_plane,
            route=subscription_start.request.route,
            requested_model=subscription_start.requested_model,
            endpoint_host=subscription.endpoint_host,
            endpoint_kind=subscription.endpoint_kind,
            credential_kind=subscription.credential_kind,
            billing_class=subscription.billing_class,
            client_identity=subscription.client_identity,
            error_kind="internal",
            error_code="forged_internal_failure",
            duration_ms=1,
        ),
    )
    api_start, api_result = _forged_attempt(
        plan,
        base_descriptor=api,
        forged_descriptor=api,
        ordinal=2,
    )
    store = FakeStore()
    assert await store.begin_provider_attempt(subscription_start)
    assert await store.complete_provider_attempt(
        subscription_start.attempt_id,
        internal_failure,
    )
    assert await store.begin_provider_attempt(api_start)
    assert await store.complete_provider_attempt(api_start.attempt_id, api_result)

    replay = await GrokWorkerBroker(
        registry={},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert replay.status == "indeterminate"
    assert replay.delegations[0].reason == "stored_attempt_conflict"
    assert replay.delegations[0].attempts == ()

    first = BrokerAttemptEvidence(
        start=subscription_start,
        persistence="replayed_terminal",
        result=internal_failure,
        harvest=BrokerHarvestStatus(
            status="not_applicable",
            reason="delegation_replayed",
        ),
    )
    second = BrokerAttemptEvidence(
        start=api_start,
        persistence="replayed_terminal",
        result=api_result,
        harvest=BrokerHarvestStatus(
            status="not_applicable",
            reason="delegation_replayed",
        ),
    )
    with pytest.raises(ValidationError, match="eligible subscription failure"):
        BrokerDelegationResult(
            delegation_id=subscription_start.delegation_id,
            delegation_key=plan.delegations[0].delegation_key,
            provider=plan.delegations[0].provider,
            route=plan.delegations[0].route,
            status="returned",
            reason="same_provider_api_returned",
            attempts=(first, second),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_version",
    ["provider-attempt-start/v1", "provider-attempt-start/v2"],
)
async def test_legacy_start_is_parseable_but_cannot_replay_as_broker_evidence(
    legacy_version,
):
    adapter = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    plan = _plan()
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: adapter},
        store=store,
        clock=lambda: NOW,
    )
    first = await broker.execute(plan)
    current = next(iter(store.starts.values()))
    raw = current.model_dump(mode="python")
    raw["version"] = legacy_version
    if legacy_version.endswith("/v1"):
        raw["execution"] = None
    else:
        for field in (
            "planning_model",
            "coding_model",
            "vision_model",
            "research_model",
            "supported_routes",
            "max_output_tokens",
            "max_timeout_seconds",
            "transport_resource_identity",
        ):
            raw["execution"].pop(field)
    legacy = ProviderAttemptStart.model_validate(raw)
    store.starts = {legacy.attempt_id: legacy}

    refused = await broker.execute(plan)

    assert first.status == "returned"
    assert legacy.version == legacy_version
    assert refused.status == "indeterminate"
    assert refused.delegations[0].reason == "stored_attempt_conflict"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_delegations_run_in_bounded_parallel():
    tracker = {"active": 0, "peak": 0}
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=0.03,
        tracker=tracker,
    )
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(count=6, fallback=False, max_concurrency=2))
    assert result.status == "returned"
    assert tracker["peak"] == 2


@pytest.mark.asyncio
async def test_expired_plan_has_no_provider_or_ledger_effect():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(_plan(ttl=NOW - timedelta(seconds=1)))
    assert result.status == "expired"
    assert adapter.calls == 0
    assert not store.starts


@pytest.mark.asyncio
async def test_broker_timeout_is_recorded_and_late_result_is_not_exposed():
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=0.2,
    )
    store = FakeStore()
    real_now = datetime.now(UTC)
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
    ).execute(_plan(fallback=False, ttl=real_now + timedelta(seconds=0.03)))
    evidence = result.delegations[0].attempts[0]
    assert result.status == "failed"
    assert evidence.result.status == "failed"
    assert evidence.result.failure.error_code == "ttl_expired"
    assert evidence.persistence == "durable_terminal"


@pytest.mark.asyncio
async def test_external_cancellation_terminalizes_started_attempt_then_propagates():
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10.0,
    )
    store = FakeStore()
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    while adapter.calls == 0:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(store.starts) == 1
    assert len(store.results) == 1
    terminal = next(iter(store.results.values()))
    assert terminal.status == "failed"
    assert terminal.failure.error_kind == "transport"
    assert terminal.failure.error_code == "broker_cancelled"


@pytest.mark.asyncio
async def test_cancellation_terminal_write_failure_is_explicit():
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10.0,
    )
    store = FakeStore(fail_complete=True)
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    while adapter.calls == 0:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(
        BrokerCancellationPersistenceError,
        match="could not be terminalized",
    ):
        await task


@pytest.mark.asyncio
async def test_cancellation_during_terminal_write_preserves_exact_provider_result():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    store = BlockingCompleteStore()
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    await store.complete_entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    store.complete_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(store.starts) == 1
    assert len(store.results) == 1
    terminal = next(iter(store.results.values()))
    assert terminal.status == "returned"
    assert terminal.response.text == "evidence from openai_mcp_sampling"


@pytest.mark.asyncio
async def test_cancelled_blocked_terminal_write_failure_stays_explicit():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    store = BlockingCompleteStore(fail_after_release=True)
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    await store.complete_entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    store.complete_release.set()
    with pytest.raises(
        BrokerCancellationPersistenceError,
        match="could not be terminalized",
    ):
        await task


@pytest.mark.asyncio
async def test_fatal_delegation_cancels_and_awaits_every_started_sibling():
    tracker = {"active": 0, "peak": 0}
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10.0,
        tracker=tracker,
    )
    store = FakeStore()

    class OneFatalBroker(GrokWorkerBroker):
        async def _guarded_delegation(self, plan, index, semaphore):
            if index == 0:
                while adapter.calls == 0:
                    await asyncio.sleep(0)
                raise BrokerCancellationPersistenceError("injected fatal persistence")
            return await super()._guarded_delegation(plan, index, semaphore)

    with pytest.raises(
        BrokerCancellationPersistenceError,
        match="injected fatal persistence",
    ):
        await OneFatalBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(count=2, fallback=False, max_concurrency=2))

    assert tracker["active"] == 0
    assert len(store.starts) == 1
    assert len(store.results) == 1
    terminal = next(iter(store.results.values()))
    assert terminal.status == "failed"
    assert terminal.failure.error_code == "broker_cancelled"


@pytest.mark.asyncio
async def test_injected_adapter_obeys_request_timeout_before_supervisor_ttl():
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=1.3,
    )
    started = time.monotonic()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(fallback=False, timeout_seconds=1.0))
    elapsed = time.monotonic() - started
    evidence = result.delegations[0].attempts[0]
    assert elapsed < 1.2
    assert evidence.result.status == "failed"
    assert evidence.result.failure.error_code == "timeout"


@pytest.mark.asyncio
async def test_injected_adapter_receives_descriptor_capped_output_budget():
    descriptor = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING).model_copy(
        update={"max_output_tokens": 1024, "max_timeout_seconds": 3.0}
    )
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        descriptor=descriptor,
    )
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(fallback=False, lane_descriptors=(descriptor,)))
    assert result.status == "returned"
    assert adapter.requests[0].max_output_tokens == 1024
    assert adapter.requests[0].timeout_seconds == 3.0


@pytest.mark.asyncio
async def test_live_descriptor_not_authorized_by_plan_is_rejected_before_begin():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(
        update={
            "endpoint_host": "forged.openai.com",
            "residency": "forged-region",
            "max_output_tokens": 512,
        }
    )
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        descriptor=changed,
    )
    store = FakeStore()

    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(
        _plan(
            fallback=False,
            lane_descriptors=(authorized,),
        )
    )

    assert result.status == "indeterminate"
    assert result.delegations[0].reason == "registry_contract_invalid"
    assert adapter.calls == 0
    assert store.starts == {}


@pytest.mark.asyncio
async def test_availability_only_descriptor_drift_remains_authorized():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    available_drift = authorized.model_copy(
        update={
            "display_name": "renamed available lane",
            "credential_state": CredentialState.DEFERRED,
        }
    )
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        descriptor=available_drift,
    )
    store = FakeStore()

    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(
        _plan(
            fallback=False,
            lane_descriptors=(authorized,),
        )
    )

    assert result.status == "returned"
    assert adapter.calls == 1
    assert len(store.starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("switch_on_read", "expected_rows", "expected_code"),
    [
        (3, 0, None),
        (4, 1, "descriptor_authorization_changed"),
    ],
)
async def test_dynamic_descriptor_switch_is_rejected_at_each_effect_edge(
    switch_on_read,
    expected_rows,
    expected_code,
):
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(update={"endpoint_host": "forged.openai.com"})

    class SwitchingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                descriptor=authorized,
            )
            self.descriptor_reads = 0

        @property
        def descriptor(self):
            self.descriptor_reads += 1
            return changed if self.descriptor_reads >= switch_on_read else authorized

    adapter = SwitchingAdapter()
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(
        _plan(
            fallback=False,
            lane_descriptors=(authorized,),
        )
    )

    assert adapter.calls == 0
    assert len(store.starts) == expected_rows
    if expected_code is None:
        assert result.status == "indeterminate"
        assert result.delegations[0].attempts == ()
    else:
        attempt = result.delegations[0].attempts[0]
        assert result.status == "failed"
        assert attempt.result.failure.error_code == expected_code


@pytest.mark.asyncio
async def test_descriptor_switch_inside_adapter_cannot_certify_returned_evidence():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(
        update={
            "models": authorized.models.model_copy(update={"planning": "gpt-forged"}),
            "data_handling": "project_policy",
            "residency": "forged-region",
        }
    )

    class DuringAttemptSwitchAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                descriptor=authorized,
            )
            self.switched = False

        @property
        def descriptor(self):
            return changed if self.switched else authorized

        async def attempt(self, request):
            self.calls += 1
            self.switched = True
            receipt = _receipt(request, authorized)
            return ProviderAttemptResult(
                status="returned",
                response=ProviderResponse(
                    provider=authorized.provider,
                    channel=authorized.channel,
                    model=receipt.resolved_model,
                    text="return that must not be certified",
                    finish_reason="stop",
                    receipt=receipt,
                ),
            )

    adapter = DuringAttemptSwitchAdapter()
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(
        _plan(
            fallback=False,
            lane_descriptors=(authorized,),
        )
    )

    attempt = result.delegations[0].attempts[0]
    assert adapter.calls == 1
    assert result.status == "failed"
    assert attempt.result.status == "failed"
    assert attempt.result.failure.error_code == "descriptor_authorization_changed"
    assert "return that must not be certified" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_queued_harvest_trigger_cannot_extend_the_shared_ttl():
    harvester = FakeHarvester(delay=0.15)
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    start = datetime.now(UTC)
    plan = _plan(
        count=2,
        fallback=False,
        max_concurrency=2,
        ttl=start + timedelta(seconds=0.2),
    )
    began = time.monotonic()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=FakeStore(),
        harvester=harvester,
        harvest_timeout_seconds=1.0,
    ).execute(plan)
    elapsed = time.monotonic() - began
    harvest_states = {
        delegation.attempts[0].harvest.status for delegation in result.delegations
    }
    assert elapsed < 0.35
    assert len(harvester.deadlines) == 2
    assert max(harvester.deadlines) - min(harvester.deadlines) < 0.03
    assert "complete" in harvest_states
    assert harvest_states & {"ttl_expired", "timed_out"}


@pytest.mark.asyncio
async def test_real_harvester_revokes_background_thread_at_grok_ttl(tmp_path):
    class BlockingUploader:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()
            self.effects: list[str] = []
            self.authority = None

        def prepare_client(self):
            return object(), None

        def upload(self, client, row, authority):
            self.authority = authority
            try:
                authority.require_active()
                self.effects.append("first_cloud_effect")
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise RuntimeError("test uploader was never released")
                authority.require_active()
                self.effects.append("forbidden_late_effect")
                return "remote-file"
            finally:
                self.finished.set()

    class CapturingHarvester(ProviderAttemptHarvester):
        deadline = None

        async def run_once(self, store, *, deadline_monotonic=None):
            self.deadline = deadline_monotonic
            return await super().run_once(
                store,
                deadline_monotonic=deadline_monotonic,
            )

    uploader = BlockingUploader()
    harvester = CapturingHarvester(
        uploader=uploader,
        batch_size=1,
        lease_seconds=5,
        call_timeout_seconds=2,
        lease_id_factory=lambda: "broker-ttl-owner",
    )
    store = GrokSessionStore(tmp_path / "broker-harvest-ttl.db")
    ttl = datetime.now(UTC) + timedelta(seconds=0.5)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING
            )
        },
        store=store,
        harvester=harvester,
        harvest_timeout_seconds=1.0,
    ).execute(_plan(fallback=False, ttl=ttl))

    evidence = result.delegations[0].attempts[0]
    assert uploader.started.is_set()
    assert evidence.harvest.status == "ttl_expired"
    assert harvester.deadline is not None
    assert uploader.authority.expires_monotonic <= harvester.deadline
    with pytest.raises(PermissionError, match="authority expired"):
        uploader.authority.require_active()

    uploader.release.set()
    for _ in range(200):
        if uploader.finished.is_set():
            break
        await asyncio.sleep(0.01)
    assert uploader.finished.is_set()
    assert uploader.effects == ["first_cloud_effect"]
    rows = await store.list_provider_attempts()
    assert rows[0]["harvest_status"] == "leased"
    assert rows[0]["remote_file_id"] is None
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harvester", "expected"),
    [
        (None, "unavailable"),
        (FakeHarvester(outcome="unavailable"), "unavailable"),
        (FakeHarvester(outcome="raise"), "failed"),
        (FakeHarvester(delay=0.2), "timed_out"),
    ],
)
async def test_harvest_state_is_honest_and_never_changes_worker_transport(
    harvester,
    expected,
):
    kwargs = {"harvest_timeout_seconds": 0.1} if expected == "timed_out" else {}
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING
            )
        },
        store=FakeStore(),
        harvester=harvester,
        clock=lambda: NOW,
        **kwargs,
    ).execute(_plan(fallback=False))
    assert result.status == "returned"
    assert result.delegations[0].attempts[0].harvest.status == expected


def test_plan_and_registry_reject_provider_channel_and_authority_spoofing():
    with pytest.raises(ValidationError):
        GrokWorkerDelegation(
            delegation_key="bad",
            provider=ProviderId.XAI,
            route=RouteClass.PLANNING,
            messages=(ProviderMessage(role="user", content="bad"),),
            fallback=_fallback(),
        )
    with pytest.raises(ValidationError, match="Grok"):
        GrokDelegationPlan(
            supervision=_plan().supervision,
            supervisor_plane="CLI",
            supervisor_model="claude-fable-5",
            delegations=_plan().delegations,
        )
    spoof_descriptor = _descriptor(ProviderChannel.ANTHROPIC_API)
    with pytest.raises(ValueError, match="registry key"):
        GrokWorkerBroker(
            registry={
                ProviderChannel.OPENAI_API: FakeAdapter(
                    ProviderChannel.ANTHROPIC_API,
                    descriptor=spoof_descriptor,
                )
            },
            store=FakeStore(),
        )

    plan_raw = _plan().model_dump(mode="python")
    plan_raw["management_credential"] = "XAI_MANAGEMENT_API_KEY"
    with pytest.raises(ValidationError, match="Extra inputs"):
        GrokDelegationPlan.model_validate(plan_raw)

    grok_models = ProviderModelPins(
        planning="grok-4.5",
        coding="grok-4.5",
        vision="grok-4.5",
        research="grok-4.5",
    )
    xai_descriptor = ProviderDescriptor(
        provider=ProviderId.XAI,
        channel=ProviderChannel.XAI_API,
        credential_plane=CredentialPlane.METERED_API,
        display_name="xAI supervisor",
        endpoint_host="api.x.ai",
        endpoint_kind="first_party_api",
        credential_kind="api_key",
        credential_env_names=(),
        credential_state=CredentialState.CONFIGURED,
        models=grok_models,
        data_handling="provider_managed",
        residency="test",
    )
    with pytest.raises(ValueError, match="supervisor channel"):
        GrokWorkerBroker(
            registry={
                ProviderChannel.XAI_API: SimpleNamespace(descriptor=xai_descriptor)
            },
            store=FakeStore(),
        )


@pytest.mark.asyncio
async def test_broker_core_uses_no_sockets_or_credential_environment(monkeypatch):
    import socket

    def blocked_socket(*args, **kwargs):
        raise AssertionError("broker attempted a live socket")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    for name in (
        "XAI_API_KEY",
        "XAI_MANAGEMENT_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.setenv(name, f"forbidden-{name}")
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING
            )
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))
    assert result.status == "returned"
    source = __import__("inspect").getsource(
        __import__("src.providers.broker", fromlist=["x"])
    )
    assert "XAI_MANAGEMENT_API_KEY" not in source
    assert "import os" not in source


def test_harvest_status_rejects_secret_shaped_or_unbounded_details():
    with pytest.raises(ValidationError):
        BrokerHarvestStatus(status="failed", reason="contains a secret value")


@pytest.mark.asyncio
async def test_exported_result_contracts_reject_inverse_and_identity_spoofs():
    plan = _plan(count=2, fallback=False)
    returned = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING
            )
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(plan)
    attempt = returned.delegations[0].attempts[0]

    raw_attempt = attempt.model_dump(mode="python")
    raw_attempt["persistence"] = "replay_indeterminate"
    raw_attempt["result"] = None
    raw_attempt["harvest"] = BrokerHarvestStatus(status="complete")
    with pytest.raises(ValidationError, match="non-durable"):
        BrokerAttemptEvidence.model_validate(raw_attempt)

    raw_delegation = returned.delegations[0].model_dump(mode="python")
    raw_delegation["status"] = "failed"
    raw_delegation["reason"] = "forged_failure"
    raw_delegation["attempts"][0]["persistence"] = "replay_indeterminate"
    raw_delegation["attempts"][0]["result"] = None
    raw_delegation["attempts"][0]["harvest"] = BrokerHarvestStatus(
        status="not_applicable",
        reason="forged_failure",
    )
    with pytest.raises(ValidationError, match="indeterminate delegations"):
        BrokerDelegationResult.model_validate(raw_delegation)

    for field, forged in (
        ("delegation_id", "dlg:" + ("f" * 64)),
        ("provider", ProviderId.ANTHROPIC),
        ("request.route", RouteClass.CODING),
    ):
        raw_delegation = returned.delegations[0].model_dump(mode="python")
        if field == "request.route":
            raw_delegation["attempts"][0]["start"]["request"]["route"] = forged
        else:
            raw_delegation["attempts"][0]["start"][field] = forged
        with pytest.raises(ValidationError):
            BrokerDelegationResult.model_validate(raw_delegation)

    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome="failed",
    )
    fallback = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: FakeAdapter(ProviderChannel.OPENAI_API),
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())
    duplicate_ordinal = fallback.delegations[0].model_dump(mode="python")
    duplicate_ordinal["attempts"][1]["start"]["attempt_ordinal"] = 1
    with pytest.raises(ValidationError, match="attempt ordinals"):
        BrokerDelegationResult.model_validate(duplicate_ordinal)

    cross_plane = attempt.model_dump(mode="python")
    cross_plane["result"] = fallback.delegations[0].attempts[1].result
    with pytest.raises(ValidationError, match="exact attempt start"):
        BrokerAttemptEvidence.model_validate(cross_plane)

    wrong_region = attempt.model_dump(mode="python")
    wrong_region["result"]["response"]["receipt"]["region"] = "forged-region"
    with pytest.raises(ValidationError, match="exact attempt start"):
        BrokerAttemptEvidence.model_validate(wrong_region)

    anthropic = await GrokWorkerBroker(
        registry={
            ProviderChannel.ANTHROPIC_API: FakeAdapter(ProviderChannel.ANTHROPIC_API)
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(ProviderId.ANTHROPIC))
    cross_provider = attempt.model_dump(mode="python")
    cross_provider["result"] = anthropic.delegations[0].attempts[0].result
    with pytest.raises(ValidationError, match="exact attempt start"):
        BrokerAttemptEvidence.model_validate(cross_provider)

    plan_spoofs = []
    raw = returned.model_dump(mode="python")
    raw["delegations"][0]["delegation_key"] = "forged-unique-key"
    plan_spoofs.append(raw)

    raw = returned.model_dump(mode="python")
    forged_attempt = raw["delegations"][0]["attempts"][0]
    forged_attempt["start"]["provider"] = ProviderId.ANTHROPIC
    forged_attempt["start"]["channel"] = ProviderChannel.ANTHROPIC_MCP_SAMPLING
    forged_attempt["result"]["response"]["provider"] = ProviderId.ANTHROPIC
    forged_attempt["result"]["response"]["channel"] = (
        ProviderChannel.ANTHROPIC_MCP_SAMPLING
    )
    forged_attempt["result"]["response"]["receipt"]["provider"] = ProviderId.ANTHROPIC
    forged_attempt["result"]["response"]["receipt"]["channel"] = (
        ProviderChannel.ANTHROPIC_MCP_SAMPLING
    )
    raw["delegations"][0]["provider"] = ProviderId.ANTHROPIC
    plan_spoofs.append(raw)

    raw = returned.model_dump(mode="python")
    forged_attempt = raw["delegations"][0]["attempts"][0]
    forged_attempt["start"]["request"]["route"] = RouteClass.CODING
    forged_attempt["result"]["response"]["receipt"]["route"] = RouteClass.CODING
    raw["delegations"][0]["route"] = RouteClass.CODING
    plan_spoofs.append(raw)

    raw = returned.model_dump(mode="python")
    raw["delegations"][0]["attempts"][0]["start"]["attempt_ordinal"] = 2
    plan_spoofs.append(raw)

    for forged in plan_spoofs:
        candidate = GrokWorkerBrokerResult.model_validate(forged)
        with pytest.raises(ValueError, match="plan"):
            candidate.validate_against_plan(plan)

    global_spoofs = []
    raw = returned.model_dump(mode="python")
    raw["plan_id"] = "gdp:" + ("0" * 64)
    global_spoofs.append(raw)
    raw = returned.model_dump(mode="python")
    raw["status"] = "failed"
    global_spoofs.append(raw)
    raw = returned.model_dump(mode="python")
    raw["delegations"][1]["delegation_key"] = raw["delegations"][0]["delegation_key"]
    global_spoofs.append(raw)
    raw = returned.model_dump(mode="python")
    duplicate_id = raw["delegations"][0]["delegation_id"]
    raw["delegations"][1]["delegation_id"] = duplicate_id
    raw["delegations"][1]["attempts"][0]["start"]["delegation_id"] = duplicate_id
    global_spoofs.append(raw)
    raw = returned.model_dump(mode="python")
    raw["supervisor_model"] = "grok-forged"
    global_spoofs.append(raw)
    raw = returned.model_dump(mode="python")
    raw["supervisor_plane"] = "API"
    global_spoofs.append(raw)
    raw = returned.model_dump(mode="python")
    raw["supervision"]["objective_id"] = "forged-objective"
    global_spoofs.append(raw)

    for forged in global_spoofs:
        with pytest.raises(ValidationError):
            GrokWorkerBrokerResult.model_validate(forged)

    expired_plan = _plan(ttl=NOW - timedelta(seconds=1))
    zero_attempt = await GrokWorkerBroker(
        registry={},
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(expired_plan)
    non_grok = zero_attempt.model_dump(mode="python")
    non_grok["supervisor_model"] = "claude-fable-5"
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        GrokWorkerBrokerResult.model_validate(non_grok)
