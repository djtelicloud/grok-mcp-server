from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import threading
from types import SimpleNamespace
from typing import Any
import time

import mcp.types as mcp_types
import pytest
from pydantic import ValidationError
from starlette.requests import Request

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
    MCP_PROVIDER_CAPABILITIES_SCOPE_KEY,
    MCP_PROVIDER_GRANTS_SCOPE_KEY,
    MCP_SESSION_AUTHORIZATION_SCOPE_KEY,
    MCP_SESSION_RUNTIME_SCOPE_KEY,
    MCPSamplingSessionRuntime,
    MCPSessionAuthorization,
    ProviderAttemptAdapterSource,
    ProviderAttemptCanonicalProjection,
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
    TrustedMCPProviderCapability,
    TrustedMCPProviderGrant,
    WorkerFallbackPolicy,
    create_stateful_mcp_sampling_lease,
    provider_request_digest,
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
        claim_state: Any = False,
        claim_on_attempt: bool = False,
    ) -> None:
        self._descriptor = descriptor or _descriptor(channel)
        self.outcome = outcome
        self.failure_kind = failure_kind
        self.delay = delay
        self.events = events if events is not None else []
        self.tracker = tracker
        self.response_text = response_text
        self.claim_state = claim_state
        self.claim_on_attempt = claim_on_attempt
        self.calls = 0
        self.requests: list[Any] = []

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def effect_claimed(self) -> bool:
        return self.claim_state

    async def complete(self, request):
        result = await self.attempt(request)
        if result.response is None:
            raise RuntimeError("fake failure")
        return result.response

    async def attempt(self, request) -> ProviderAttemptResult:
        if self.claim_on_attempt:
            self.claim_state = True
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


class FakeAdapterSource:
    def __init__(
        self,
        adapter: FakeAdapter,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.adapter = adapter
        self.events = events if events is not None else []
        self.opens = 0
        self.closes = 0
        self.starts: list[ProviderAttemptStart] = []

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self.adapter.descriptor

    @asynccontextmanager
    async def open_attempt(self, start: ProviderAttemptStart):
        self.opens += 1
        self.starts.append(start)
        self.events.append(f"open:{start.channel.value}")
        try:
            yield self.adapter
        finally:
            self.closes += 1
            self.events.append(f"close:{start.channel.value}")


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
        self.canonical_result_digests: dict[str, str] = {}
        self.canonical_projections: dict[
            str, ProviderAttemptCanonicalProjection
        ] = {}
        self.projection_leases: set[str] = set()

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
        self.projection_leases.add(start.attempt_id)
        return True

    def canonical_provider_attempt_result(
        self,
        attempt_id,
        result,
        redaction_snapshot=None,
    ):
        del redaction_snapshot
        if attempt_id not in self.projection_leases:
            raise PermissionError("no live canonical-projection lease")
        self.projection_leases.remove(attempt_id)
        projected = (
            ProviderAttemptResult.model_validate_json(
                result.model_dump_json(warnings="error")
            )
            if isinstance(result, ProviderAttemptResult)
            else ProviderAttemptResult.model_validate(result)
        )
        result_digest = provider_broker._provider_result_digest(projected)
        nonce = len(self.canonical_projections)
        material = json.dumps(
            [attempt_id, result_digest, nonce, id(self)],
            separators=(",", ":"),
        )
        authorization_tag = "hmac-sha256:" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()
        projection = ProviderAttemptCanonicalProjection(
            attempt_id=attempt_id,
            result=projected,
            result_digest=result_digest,
            output_redaction=("clean" if projected.response is not None else None),
            authorization_tag=authorization_tag,
        )
        self.canonical_projections[authorization_tag] = projection
        return projection

    async def revoke_provider_attempt_projection(self, attempt_id):
        self.projection_leases.discard(attempt_id)
        revoked = [
            tag
            for tag, projection in self.canonical_projections.items()
            if projection.attempt_id == attempt_id
        ]
        for tag in revoked:
            self.canonical_projections.pop(tag, None)

    async def complete_provider_attempt(
        self,
        attempt_id,
        result,
    ) -> bool:
        del attempt_id, result
        raise PermissionError("direct provider-attempt completion is disabled")

    async def complete_projected_provider_attempt(
        self,
        attempt_id,
        projection,
    ) -> bool:
        self.events.append(f"complete:{self.starts[attempt_id].channel.value}")
        if self.fail_complete:
            raise RuntimeError("terminal unavailable")
        projection = (
            ProviderAttemptCanonicalProjection.model_validate_json(
                projection.model_dump_json(warnings="error")
            )
            if isinstance(projection, ProviderAttemptCanonicalProjection)
            else ProviderAttemptCanonicalProjection.model_validate(projection)
        )
        approved = self.canonical_projections.get(projection.authorization_tag)
        if approved != projection or projection.attempt_id != attempt_id:
            raise ValueError("canonical projection authorization mismatch")
        result = projection.result
        approved_digest = projection.result_digest
        existing = self.results.get(attempt_id)
        if existing is not None:
            if (
                existing != result
                or self.canonical_result_digests.get(attempt_id)
                != approved_digest
            ):
                raise ValueError("terminal conflict")
            self.canonical_projections.pop(projection.authorization_tag, None)
            return False
        self.results[attempt_id] = result
        self.canonical_result_digests[attempt_id] = approved_digest
        self.canonical_projections.pop(projection.authorization_tag, None)
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
                    "canonical_result_digest": (
                        self.canonical_result_digests[attempt_id]
                    ),
                }
            )
        return rows[:limit]


class BlockingCompleteStore(FakeStore):
    def __init__(
        self,
        *,
        fail_after_release: bool = False,
        events: list[str] | None = None,
    ) -> None:
        super().__init__(events=events)
        self.fail_after_release = fail_after_release
        self.complete_entered = asyncio.Event()
        self.complete_release = asyncio.Event()

    async def complete_projected_provider_attempt(
        self,
        attempt_id,
        projection,
    ) -> bool:
        self.events.append(f"complete:{self.starts[attempt_id].channel.value}")
        self.complete_entered.set()
        await self.complete_release.wait()
        if self.fail_after_release:
            raise RuntimeError("terminal write failed after release")
        projection = (
            ProviderAttemptCanonicalProjection.model_validate_json(
                projection.model_dump_json(warnings="error")
            )
            if isinstance(projection, ProviderAttemptCanonicalProjection)
            else ProviderAttemptCanonicalProjection.model_validate(projection)
        )
        approved = self.canonical_projections.get(projection.authorization_tag)
        if approved != projection or projection.attempt_id != attempt_id:
            raise ValueError("canonical projection authorization mismatch")
        result = projection.result
        approved_digest = projection.result_digest
        existing = self.results.get(attempt_id)
        if existing is not None:
            if (
                existing != result
                or self.canonical_result_digests.get(attempt_id)
                != approved_digest
            ):
                raise ValueError("terminal conflict")
            self.canonical_projections.pop(projection.authorization_tag, None)
            return False
        self.results[attempt_id] = result
        self.canonical_result_digests[attempt_id] = approved_digest
        self.canonical_projections.pop(projection.authorization_tag, None)
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
    route: RouteClass = RouteClass.PLANNING,
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
                route=route,
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
async def test_attempt_adapter_source_opens_only_after_new_durable_begin():
    events: list[str] = []
    source_impl = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING, events=events),
        events=events,
    )
    source: ProviderAttemptAdapterSource = source_impl
    store = FakeStore(events=events)

    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=store,
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))

    assert result.status == "returned"
    assert events == [
        "begin:openai_mcp_sampling",
        "open:openai_mcp_sampling",
        "effect:openai_mcp_sampling",
        "close:openai_mcp_sampling",
        "complete:openai_mcp_sampling",
    ]
    assert source_impl.opens == source_impl.closes == 1
    assert source_impl.starts == [next(iter(store.starts.values()))]


@pytest.mark.asyncio
async def test_attempt_adapter_source_never_opens_on_begin_failure_or_replay():
    failed_source = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    )
    failed = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: failed_source},
        store=FakeStore(fail_begin=True),
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))
    assert failed.status == "indeterminate"
    assert failed_source.opens == 0

    plan = _plan(fallback=False)
    store = FakeStore()
    first_source = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    )
    first = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: first_source},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    replay_source = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    )
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: replay_source},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert first.status == replay.status == "returned"
    assert first_source.opens == 1
    assert replay_source.opens == 0
    assert replay.delegations[0].attempts[0].persistence == "replayed_terminal"


@pytest.mark.asyncio
async def test_attempt_adapter_source_never_opens_when_ttl_expires_during_begin():
    clock_now = [NOW]

    class ExpiringBeginStore(FakeStore):
        async def begin_provider_attempt(self, start):
            is_new = await super().begin_provider_attempt(start)
            clock_now[0] = NOW + timedelta(seconds=2)
            return is_new

    source = FakeAdapterSource(FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING))
    store = ExpiringBeginStore()
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: source,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: clock_now[0],
    ).execute(_plan(fallback=False, ttl=NOW + timedelta(seconds=1)))

    assert result.status == "failed"
    assert source.opens == 0
    assert api.calls == 0
    terminal = next(iter(store.results.values()))
    assert terminal.failure.error_code == "ttl_expired"


@pytest.mark.asyncio
async def test_static_adapter_and_attempt_source_preserve_attempt_identity():
    descriptor = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    plan = _plan(fallback=False, lane_descriptors=(descriptor,))
    static = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                descriptor=descriptor,
            )
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(plan)
    sourced = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapterSource(
                FakeAdapter(
                    ProviderChannel.OPENAI_MCP_SAMPLING,
                    descriptor=descriptor,
                )
            )
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(plan)

    static_start = static.delegations[0].attempts[0].start
    sourced_start = sourced.delegations[0].attempts[0].start
    assert static.status == sourced.status == "returned"
    assert static_start == sourced_start
    assert static_start.request.request_id == sourced_start.request.request_id
    assert static_start.execution == sourced_start.execution


@pytest.mark.asyncio
async def test_started_attempt_replay_never_reopens_dynamic_source():
    plan = _plan(fallback=False)
    store = FakeStore(fail_complete=True)
    first_source = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    )
    first = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: first_source},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    replay_source = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    )
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: replay_source},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert first.status == replay.status == "indeterminate"
    assert first_source.opens == 1
    assert replay_source.opens == 0
    assert replay.delegations[0].reason == "stored_attempt_indeterminate"


def test_registry_rejects_ambiguous_adapter_and_source_surface():
    class AmbiguousEntry(FakeAdapter):
        @asynccontextmanager
        async def open_attempt(self, start):
            del start
            yield self

    with pytest.raises(ValueError, match="exactly one adapter or adapter source"):
        GrokWorkerBroker(
            registry={
                ProviderChannel.OPENAI_MCP_SAMPLING: AmbiguousEntry(
                    ProviderChannel.OPENAI_MCP_SAMPLING
                )
            },
            store=FakeStore(),
        )


@pytest.mark.asyncio
async def test_source_entry_consumes_same_absolute_attempt_deadline():
    events: list[str] = []
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)

    class BlockingEntrySource:
        descriptor = adapter.descriptor

        def __init__(self):
            self.entered = 0
            self.exits = 0
            self.acquired = False

        def open_attempt(self, start):
            del start
            source = self

            class Manager:
                async def __aenter__(self):
                    source.entered += 1
                    source.acquired = True
                    events.append("entry_acquired:openai_mcp_sampling")
                    await asyncio.sleep(10)
                    return adapter

                async def __aexit__(self, exc_type, exc, traceback):
                    del exc_type, exc, traceback
                    source.exits += 1
                    source.acquired = False
                    events.append("entry_revoked:openai_mcp_sampling")
                    return None

            return Manager()

    source = BlockingEntrySource()
    started = time.monotonic()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=FakeStore(events=events),
    ).execute(
        _plan(
            fallback=False,
            ttl=datetime.now(UTC) + timedelta(seconds=0.05),
        )
    )

    assert time.monotonic() - started < 0.5
    assert result.status == "failed"
    assert source.entered == 1
    assert source.exits == 1
    assert source.acquired is False
    assert adapter.calls == 0
    assert events.index("entry_revoked:openai_mcp_sampling") < events.index(
        "complete:openai_mcp_sampling"
    )
    assert result.delegations[0].attempts[0].result.failure.error_code == (
        "ttl_expired"
    )


@pytest.mark.asyncio
async def test_partial_source_entry_cleanup_failure_stays_nonterminal():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)

    class UnsafePartialEntrySource:
        descriptor = adapter.descriptor

        def open_attempt(self, start):
            del start

            class Manager:
                async def __aenter__(self):
                    await asyncio.sleep(10)
                    return adapter

                async def __aexit__(self, exc_type, exc, traceback):
                    del exc_type, exc, traceback
                    raise RuntimeError("partial authority revocation failed")

            return Manager()

    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: UnsafePartialEntrySource()
        },
        store=store,
    ).execute(
        _plan(
            fallback=False,
            ttl=datetime.now(UTC) + timedelta(seconds=0.05),
        )
    )

    evidence = result.delegations[0].attempts[0]
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.harvest.reason == "adapter_source_cleanup_failed"
    assert store.results == {}
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_blocking_adapter_return_after_monotonic_deadline_is_not_certified():
    class BlockingReturnAdapter(FakeAdapter):
        async def attempt(self, request):
            result = await super().attempt(request)
            time.sleep(1.1)
            return result

    subscription = BlockingReturnAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        claim_on_attempt=True,
        response_text="late output must not be certified",
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(_plan(timeout_seconds=1.0))

    evidence = result.delegations[0].attempts[0]
    assert result.status == "failed"
    assert evidence.result.failure.error_kind == "internal"
    assert evidence.result.failure.error_code == "sampling_effect_indeterminate"
    assert "late output must not be certified" not in result.model_dump_json()
    assert subscription.calls == 1
    assert api.calls == 0
    assert len(store.results) == 1


@pytest.mark.asyncio
async def test_ttl_is_rechecked_after_source_context_enter_before_effect():
    clock_now = [NOW]
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)

    class ExpiringEnterSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            clock_now[0] = NOW + timedelta(seconds=2)
            try:
                yield self.adapter
            finally:
                self.closes += 1

    source = ExpiringEnterSource(adapter)
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=FakeStore(),
        clock=lambda: clock_now[0],
    ).execute(
        _plan(
            fallback=False,
            ttl=NOW + timedelta(seconds=1),
        )
    )

    assert result.status == "failed"
    assert adapter.calls == 0
    assert source.opens == source.closes == 1
    assert result.delegations[0].attempts[0].result.failure.error_code == (
        "ttl_expired"
    )


@pytest.mark.asyncio
async def test_source_start_mutation_is_rejected_before_effect():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)

    class MutatingStartSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            object.__setattr__(start.request, "request_id", "forged-request")
            try:
                yield self.adapter
            finally:
                self.closes += 1

    source = MutatingStartSource(adapter)
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))

    assert result.status == "failed"
    assert adapter.calls == 0
    assert source.opens == source.closes == 1
    assert result.delegations[0].attempts[0].result.failure.error_code == (
        "adapter_source_start_mutation"
    )


@pytest.mark.asyncio
async def test_source_descriptor_change_during_begin_never_opens_or_falls_back():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(update={"endpoint_host": "forged.openai.com"})

    class SwitchingSource(FakeAdapterSource):
        switched = False

        @property
        def descriptor(self):
            return changed if self.switched else authorized

    source = SwitchingSource(
        FakeAdapter(
            ProviderChannel.OPENAI_MCP_SAMPLING,
            descriptor=authorized,
        )
    )

    class SwitchingBeginStore(FakeStore):
        async def begin_provider_attempt(self, start):
            is_new = await super().begin_provider_attempt(start)
            source.switched = True
            return is_new

    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: source,
            ProviderChannel.OPENAI_API: api,
        },
        store=SwitchingBeginStore(),
        clock=lambda: NOW,
    ).execute(_plan(lane_descriptors=(authorized, api.descriptor)))

    assert result.status == "failed"
    assert source.opens == 0
    assert api.calls == 0
    assert result.delegations[0].attempts[0].result.failure.error_code == (
        "adapter_source_descriptor_changed"
    )


@pytest.mark.asyncio
async def test_source_post_close_descriptor_mutation_stays_indeterminate():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(update={"endpoint_host": "forged.openai.com"})
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        descriptor=authorized,
        response_text="uncertified source output",
    )

    class PostCloseMutationSource(FakeAdapterSource):
        switched = False

        @property
        def descriptor(self):
            return changed if self.switched else authorized

        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            try:
                yield self.adapter
            finally:
                self.switched = True
                self.closes += 1

    source = PostCloseMutationSource(adapter)
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: source,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(
        _plan(lane_descriptors=(authorized, api.descriptor))
    )

    assert result.status == "indeterminate"
    assert adapter.calls == 1
    assert api.calls == 0
    assert "uncertified source output" not in result.model_dump_json()
    evidence = result.delegations[0].attempts[0]
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.reason == (
        "terminal_authority_changed_after_projection"
    )


@pytest.mark.asyncio
async def test_raw_cancellation_waits_for_one_close_before_terminal_persistence():
    events: list[str] = []
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10,
        events=events,
    )
    close_started = asyncio.Event()
    close_release = asyncio.Event()

    class BlockingCloseSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            self.events.append("open:openai_mcp_sampling")
            try:
                yield self.adapter
            finally:
                self.closes += 1
                self.events.append("close_started:openai_mcp_sampling")
                close_started.set()
                await close_release.wait()
                self.events.append("close_done:openai_mcp_sampling")

    source = BlockingCloseSource(adapter, events=events)
    store = FakeStore(events=events)
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    async with asyncio.timeout(1):
        while adapter.calls == 0:
            await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(close_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert store.results == {}

    close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert source.closes == 1
    assert len(store.results) == 1
    assert events.index("close_done:openai_mcp_sampling") < events.index(
        "complete:openai_mcp_sampling"
    )
    assert next(iter(store.results.values())).failure.error_code == (
        "broker_cancelled"
    )


@pytest.mark.asyncio
async def test_source_cleanup_failure_is_explicit_and_never_api_falls_back():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)

    class FailingCloseSource(FakeAdapterSource):
        authority_live = False

        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            self.authority_live = True
            try:
                yield self.adapter
            finally:
                self.closes += 1
                raise RuntimeError("private cleanup failure")

    source = FailingCloseSource(adapter)
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: source,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(_plan())

    evidence = result.delegations[0].attempts[0]
    assert result.status == "indeterminate"
    assert source.opens == source.closes == 1
    assert source.authority_live is True
    assert api.calls == 0
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.reason == "adapter_source_cleanup_failed"
    assert store.results == {}


@pytest.mark.asyncio
async def test_cancelled_cleanup_failure_refuses_terminal_persistence():
    events: list[str] = []
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10,
        events=events,
    )

    class FailingCancelledCloseSource(FakeAdapterSource):
        authority_live = False

        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            self.events.append("open:openai_mcp_sampling")
            self.authority_live = True
            try:
                yield self.adapter
            finally:
                self.closes += 1
                self.events.append("close:openai_mcp_sampling")
                raise RuntimeError("private cancelled cleanup failure")

    source = FailingCancelledCloseSource(adapter, events=events)
    store = FakeStore(events=events)
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    async with asyncio.timeout(1):
        while adapter.calls == 0:
            await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(
        BrokerCancellationPersistenceError,
        match="cleanup did not complete",
    ):
        await asyncio.wait_for(task, timeout=1)

    assert source.closes == 1
    assert source.authority_live is True
    assert store.results == {}
    assert "complete:openai_mcp_sampling" not in events


@pytest.mark.asyncio
async def test_source_cleanup_timeout_is_nonterminal_and_never_falls_back():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    cleanup_cancelled = asyncio.Event()

    class HangingCloseSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            try:
                yield self.adapter
            finally:
                self.closes += 1
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_cancelled.set()

    source = HangingCloseSource(adapter)
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: source,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
        adapter_cleanup_timeout_seconds=0.1,
    ).execute(_plan())

    await asyncio.wait_for(cleanup_cancelled.wait(), timeout=1)
    assert result.status == "indeterminate"
    assert result.delegations[0].reason == "attempt_state_indeterminate"
    evidence = result.delegations[0].attempts[0]
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.harvest.reason == "adapter_source_cleanup_timed_out"
    assert store.results == {}
    assert api.calls == 0


@pytest.mark.asyncio
async def test_blocking_source_cleanup_cannot_overrun_absolute_bound():
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)

    class BlockingCloseSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            try:
                yield self.adapter
            finally:
                self.closes += 1
                time.sleep(0.5)

    source = BlockingCloseSource(adapter)
    store = FakeStore()
    started = time.monotonic()
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=store,
        clock=lambda: NOW,
        adapter_cleanup_timeout_seconds=0.1,
    ).execute(_plan(fallback=False))

    evidence = result.delegations[0].attempts[0]
    assert time.monotonic() - started >= 0.5
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.harvest.reason == "adapter_source_cleanup_timed_out"
    assert store.results == {}


@pytest.mark.asyncio
async def test_cancelled_cleanup_timeout_is_explicit_and_never_terminalizes():
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10,
    )
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()

    class HangingCancelledCloseSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            try:
                yield self.adapter
            finally:
                self.closes += 1
                cleanup_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_cancelled.set()

    source = HangingCancelledCloseSource(adapter)
    store = FakeStore()
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
            store=store,
            clock=lambda: NOW,
            adapter_cleanup_timeout_seconds=0.1,
        ).execute(_plan(fallback=False))
    )
    async with asyncio.timeout(1):
        while adapter.calls == 0:
            await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)

    with pytest.raises(
        BrokerCancellationPersistenceError,
        match="cleanup did not complete",
    ):
        await asyncio.wait_for(task, timeout=1)

    await asyncio.wait_for(cleanup_cancelled.wait(), timeout=1)
    assert source.closes == 1
    assert store.results == {}


@pytest.mark.asyncio
async def test_source_open_failure_is_internal_and_never_api_falls_back():
    descriptor = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)

    class FailingOpenSource:
        @property
        def descriptor(self):
            return descriptor

        def open_attempt(self, start):
            del start

            class Manager:
                async def __aenter__(self):
                    raise RuntimeError("private open failure")

                async def __aexit__(self, exc_type, exc, traceback):
                    del exc_type, exc, traceback
                    return None

            return Manager()

    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FailingOpenSource(),
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())

    assert result.status == "failed"
    assert api.calls == 0
    failure = result.delegations[0].attempts[0].result.failure
    assert failure.error_kind == "internal"
    assert failure.error_code == "adapter_source_open_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("inspector_mode", ["missing", "throws", "non_bool"])
async def test_dynamic_source_unknown_mcp_claim_state_never_falls_back(
    inspector_mode,
):
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    if inspector_mode == "missing":
        adapter.effect_claimed = None
    elif inspector_mode == "throws":

        def broken_inspector():
            raise RuntimeError("private claim state failure")

        adapter.effect_claimed = broken_inspector
    else:
        adapter.effect_claimed = lambda: "false"
    source = FakeAdapterSource(adapter)
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: source,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())

    failure = result.delegations[0].attempts[0].result.failure
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert adapter.calls == 0
    assert source.opens == source.closes == 1
    assert api.calls == 0


@pytest.mark.asyncio
async def test_real_capability_source_mints_grant_only_after_durable_begin():
    events: list[str] = []
    authorization = MCPSessionAuthorization(
        binding_id="binding-broker-1",
        mcp_session_id="mcp-session-broker-1",
        principal="http:anon",
        client_label="antigravity",
        mcp_client_name="Antigravity",
        trust="verified_local",
        issued_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(minutes=10),
    )
    models = ProviderModelPins(
        planning="gemini-planning",
        coding="gemini-coding",
        vision="gemini-vision",
        research="gemini-research",
    )
    capability = TrustedMCPProviderCapability(
        session_authorization_digest=authorization.authorization_digest,
        provider=ProviderId.GOOGLE,
        channel=ProviderChannel.GOOGLE_MCP_SAMPLING,
        models=models,
        supported_routes=(
            RouteClass.PLANNING,
            RouteClass.CODING,
            RouteClass.RESEARCH,
        ),
    )
    pregrant_descriptor = capability.descriptor
    pregrant_lane = GrokWorkerLaneAuthorization.from_descriptor(
        pregrant_descriptor
    )
    runtime = MCPSamplingSessionRuntime(authorization)

    class Session:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []
            self.client_params = SimpleNamespace(
                capabilities=SimpleNamespace(sampling=object()),
                clientInfo=SimpleNamespace(name="Antigravity"),
            )

        async def create_message(self, **kwargs):
            self.calls.append(kwargs)
            events.append("effect:google_mcp_sampling")
            return mcp_types.CreateMessageResult(
                role="assistant",
                content=mcp_types.TextContent(
                    type="text",
                    text="Lease-owned broker observation.",
                ),
                model=models.coding,
                stopReason="endTurn",
            )

    session = Session()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (b"mcp-session-id", authorization.mcp_session_id.encode()),
            (b"x-client-id", authorization.client_label.encode()),
        ],
        MCP_SESSION_AUTHORIZATION_SCOPE_KEY: authorization,
        MCP_PROVIDER_CAPABILITIES_SCOPE_KEY: (capability,),
        MCP_PROVIDER_GRANTS_SCOPE_KEY: (),
        MCP_SESSION_RUNTIME_SCOPE_KEY: runtime,
    }
    request = Request(scope)
    related_request_id = "tool-request-77"
    request_context = SimpleNamespace(
        request=request,
        session=session,
        request_id=related_request_id,
    )
    ctx = SimpleNamespace(
        fastmcp=SimpleNamespace(settings=SimpleNamespace(stateless_http=False)),
        request_context=request_context,
        session=session,
    )
    store = FakeStore(events=events)

    class CapabilitySource:
        def __init__(self):
            self.starts: list[ProviderAttemptStart] = []
            self.grants: list[TrustedMCPProviderGrant] = []
            self.adapter_descriptor: ProviderDescriptor | None = None
            self.binding_route: RouteClass | None = None

        @property
        def descriptor(self):
            return capability.descriptor

        @asynccontextmanager
        async def open_attempt(self, start):
            assert start.attempt_id in store.starts
            assert store.starts[start.attempt_id] == start
            assert scope[MCP_PROVIDER_GRANTS_SCOPE_KEY] == ()
            self.starts.append(start)
            events.append("open:google_mcp_sampling")
            grant = TrustedMCPProviderGrant(
                grant_id="grant-" + start.attempt_id[-64:],
                session_authorization_digest=authorization.authorization_digest,
                session_capability_digest=capability.capability_digest,
                supervision=start.request.supervision,
                provider_request_id=start.request.request_id,
                provider_request_digest=provider_request_digest(start.request),
                mcp_related_request_id=related_request_id,
                provider=start.provider,
                channel=start.channel,
                route=start.request.route,
                models=capability.models,
                issued_at=NOW - timedelta(seconds=1),
                expires_at=NOW + timedelta(minutes=2),
            )
            self.grants.append(grant)
            scope[MCP_PROVIDER_GRANTS_SCOPE_KEY] = (grant,)
            lease = create_stateful_mcp_sampling_lease(
                ctx,
                provider=start.provider,
                channel=start.channel,
                provider_request=start.request,
                clock=lambda: NOW,
            )
            try:
                async with lease:
                    adapter = lease.adapter
                    self.adapter_descriptor = adapter.descriptor
                    authority = object.__getattribute__(
                        adapter,
                        "_sampling_authority",
                    )
                    self.binding_route = authority.route
                    yield adapter
            finally:
                scope[MCP_PROVIDER_GRANTS_SCOPE_KEY] = ()
                events.append("close:google_mcp_sampling")

    source = CapabilitySource()
    plan = _plan(
        ProviderId.GOOGLE,
        route=RouteClass.CODING,
        fallback=False,
        lane_descriptors=(pregrant_descriptor,),
    )
    result = await GrokWorkerBroker(
        registry={ProviderChannel.GOOGLE_MCP_SAMPLING: source},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    assert result.status == "returned"
    assert events == [
        "begin:google_mcp_sampling",
        "open:google_mcp_sampling",
        "effect:google_mcp_sampling",
        "close:google_mcp_sampling",
        "complete:google_mcp_sampling",
    ]
    start = next(iter(store.starts.values()))
    assert source.starts == [start]
    assert len(source.grants) == 1
    assert source.grants[0].provider_request_id == start.request.request_id
    assert source.grants[0].route == source.binding_route == RouteClass.CODING
    assert source.adapter_descriptor == pregrant_descriptor
    assert (
        GrokWorkerLaneAuthorization.from_descriptor(source.adapter_descriptor)
        == pregrant_lane
    )
    assert set(source.adapter_descriptor.supported_routes) == {
        RouteClass.PLANNING,
        RouteClass.CODING,
        RouteClass.RESEARCH,
    }
    assert start.request.model == models.coding
    assert session.calls[0]["model_preferences"].hints[0].name == models.coding


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
@pytest.mark.parametrize(
    ("claim_on_attempt", "expected_api_calls", "expected_kind", "expected_code"),
    [
        (True, 0, "internal", "sampling_effect_indeterminate"),
        (False, 1, "transport", "timeout"),
    ],
)
async def test_mcp_sampling_outer_timeout_uses_claim_state_before_api_fallback(
    claim_on_attempt,
    expected_api_calls,
    expected_kind,
    expected_code,
):
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=1.2,
        claim_on_attempt=claim_on_attempt,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(timeout_seconds=1.0))

    first = result.delegations[0].attempts[0]
    assert subscription.calls == 1
    assert api.calls == expected_api_calls
    assert first.result.failure.error_kind == expected_kind
    assert first.result.failure.error_code == expected_code
    assert len(result.delegations[0].attempts) == 1 + expected_api_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["configuration", "transport", "protocol"])
async def test_claimed_mcp_adapter_failure_is_internal_and_never_falls_back(
    failure_kind,
):
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome="failed",
        failure_kind=failure_kind,
        claim_on_attempt=True,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    plan = _plan()
    broker = GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    )

    first = await broker.execute(plan)
    replay = await broker.execute(plan)

    for result in (first, replay):
        evidence = result.delegations[0].attempts[0]
        assert result.status == "failed"
        assert evidence.result.failure.error_kind == "internal"
        assert evidence.result.failure.error_code == "sampling_effect_indeterminate"
    assert first.delegations[0].attempts[0].persistence == "durable_terminal"
    assert replay.delegations[0].attempts[0].persistence == "replayed_terminal"
    assert subscription.calls == 1
    assert api.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("inspector_mode", ["missing", "throws", "non_bool"])
async def test_mcp_sampling_inspector_ambiguity_fails_closed(inspector_mode):
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome="failed",
        failure_kind="transport",
    )
    if inspector_mode == "missing":
        subscription.effect_claimed = None
    elif inspector_mode == "throws":

        def broken_inspector():
            raise RuntimeError("untrusted inspector failure")

        subscription.effect_claimed = broken_inspector
    else:
        subscription.effect_claimed = lambda: "false"
    api = FakeAdapter(ProviderChannel.OPENAI_API)

    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())

    failure = result.delegations[0].attempts[0].result.failure
    assert result.status == "failed"
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert subscription.calls == 0
    assert api.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("inspector_mode", ["missing", "throws", "non_bool"])
async def test_mcp_sampling_inspector_ambiguity_after_dispatch_fails_closed(
    inspector_mode,
):
    class CorruptingInspectorAdapter(FakeAdapter):
        async def attempt(self, request):
            result = await super().attempt(request)
            if inspector_mode == "missing":
                self.effect_claimed = None
            elif inspector_mode == "throws":

                def broken_inspector():
                    raise RuntimeError("post-dispatch inspector failure")

                self.effect_claimed = broken_inspector
            else:
                self.effect_claimed = lambda: "true"
            return result

    subscription = CorruptingInspectorAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome="failed",
        failure_kind="transport",
        claim_on_attempt=True,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())

    failure = result.delegations[0].attempts[0].result.failure
    assert result.status == "failed"
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert subscription.calls == 1
    assert api.calls == 0


@pytest.mark.asyncio
async def test_non_mcp_subscription_failure_behavior_is_unchanged():
    cli = FakeAdapter(
        ProviderChannel.CLAUDE_CLI,
        outcome="failed",
        failure_kind="transport",
        claim_state=True,
    )
    api = FakeAdapter(ProviderChannel.ANTHROPIC_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.CLAUDE_CLI: cli,
            ProviderChannel.ANTHROPIC_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(ProviderId.ANTHROPIC))

    first = result.delegations[0].attempts[0]
    assert result.status == "returned"
    assert first.result.failure.error_kind == "transport"
    assert first.result.failure.error_code == "test_failure"
    assert cli.calls == 1
    assert api.calls == 1


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
    store = FakeStore(fail_complete=True)
    result = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        harvester=harvester,
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))

    evidence = result.delegations[0].attempts[0]
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.status == "not_applicable"
    assert harvester.calls == 0
    assert store.projection_leases == set()
    assert store.canonical_projections == {}


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
@pytest.mark.parametrize("outcome", ["raise", "spoof"])
async def test_claimed_mcp_exception_or_result_mismatch_is_indeterminate(outcome):
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome=outcome,
        claim_on_attempt=True,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())

    failure = result.delegations[0].attempts[0].result.failure
    assert result.status == "failed"
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert api.calls == 0


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
    assert store._provider_projection_leases == {}
    assert store._provider_projection_generations == {}
    assert store._provider_projection_authorizations == {}
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
async def test_broker_projection_survives_secret_rotation_before_atomic_write(
    tmp_path,
    monkeypatch,
):
    old_secret = "old-local-gateway-secret-12345"
    new_secret = "new-local-gateway-secret-67890"
    monkeypatch.setenv("UNIGROK_API_KEYS", old_secret)

    class RotatingProjectionStore(GrokSessionStore):
        def __init__(self, path):
            super().__init__(path)
            self.projection_calls = 0

        def canonical_provider_attempt_result(
            self,
            attempt_id,
            result,
            redaction_snapshot=None,
        ):
            projected = super().canonical_provider_attempt_result(
                attempt_id,
                result,
                redaction_snapshot,
            )
            self.projection_calls += 1
            if self.projection_calls == 1:
                monkeypatch.setenv("UNIGROK_API_KEYS", new_secret)
            return projected

    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        response_text=f"Worker echoed {old_secret}",
    )
    store = RotatingProjectionStore(tmp_path / "broker-rotated-secret.db")
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)

    first = await broker.execute(plan)
    replay = await broker.execute(plan)
    async with store._conn.execute(
        "SELECT output_text, completion_json FROM provider_attempts"
    ) as cursor:
        raw_row = await cursor.fetchone()

    assert first.status == replay.status == "returned"
    assert adapter.calls == 1
    assert store.projection_calls == 1
    assert first.delegations[0].attempts[0].result == (
        replay.delegations[0].attempts[0].result
    )
    assert old_secret not in first.model_dump_json()
    assert old_secret not in replay.model_dump_json()
    assert old_secret not in str(tuple(raw_row))
    await store.close()


@pytest.mark.asyncio
async def test_blocking_projection_never_delays_source_cleanup_or_terminalizes():
    projection_started = threading.Event()
    projection_release = threading.Event()
    projection_finished = threading.Event()

    class BlockingProjectionStore(FakeStore):
        def canonical_provider_attempt_result(
            self,
            attempt_id,
            result,
            redaction_snapshot=None,
        ):
            projection_started.set()
            projection_release.wait()
            try:
                return super().canonical_provider_attempt_result(
                    attempt_id,
                    result,
                    redaction_snapshot,
                )
            finally:
                projection_finished.set()

    source = FakeAdapterSource(
        FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    )
    store = BlockingProjectionStore()
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(
        fallback=False,
        ttl=NOW + timedelta(seconds=0.15),
    )
    started_at = time.monotonic()
    execution = asyncio.create_task(broker.execute(plan))
    try:
        assert await asyncio.to_thread(projection_started.wait, 1.0)
        async with asyncio.timeout(0.1):
            while source.closes != 1:
                await asyncio.sleep(0)
        assert not projection_release.is_set()
        result = await asyncio.wait_for(execution, timeout=1.0)
    finally:
        projection_release.set()
        if not execution.done():
            execution.cancel()
            with pytest.raises(asyncio.CancelledError):
                await execution

    assert await asyncio.to_thread(projection_finished.wait, 1.0)

    evidence = result.delegations[0].attempts[0]
    assert time.monotonic() - started_at < 0.75
    assert source.opens == source.closes == 1
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.reason == "terminal_projection_timed_out"
    assert store.results == {}
    assert store.projection_leases == set()
    assert store.canonical_projections == {}


@pytest.mark.asyncio
async def test_dynamic_source_cleanup_cannot_rotate_past_result_redaction(
    tmp_path,
    monkeypatch,
):
    old_secret = "old-dynamic-source-secret-12345"
    new_secret = "new-dynamic-source-secret-67890"
    monkeypatch.setenv("UNIGROK_API_KEYS", old_secret)
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        response_text=f"Worker echoed {old_secret}",
    )

    class RotatingCloseSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            try:
                yield self.adapter
            finally:
                self.closes += 1
                monkeypatch.setenv("UNIGROK_API_KEYS", new_secret)

    source = RotatingCloseSource(adapter)
    store = GrokSessionStore(tmp_path / "broker-cleanup-rotation.db")
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)

    first = await broker.execute(plan)
    replay = await broker.execute(plan)
    async with store._conn.execute(
        "SELECT output_text, completion_json FROM provider_attempts"
    ) as cursor:
        raw_row = await cursor.fetchone()

    assert first.status == replay.status == "returned"
    assert adapter.calls == 1
    assert source.opens == source.closes == 1
    assert first.delegations[0].attempts[0].result == (
        replay.delegations[0].attempts[0].result
    )
    assert old_secret not in first.model_dump_json()
    assert old_secret not in replay.model_dump_json()
    assert old_secret not in str(tuple(raw_row))
    await store.close()


@pytest.mark.asyncio
async def test_entry_rotation_is_captured_before_cleanup_and_never_persisted(
    tmp_path,
    monkeypatch,
):
    begin_secret = "begin-source-secret-12345"
    active_secret = "active-source-secret-67890"
    closed_secret = "closed-source-secret-24680"
    monkeypatch.setenv("UNIGROK_API_KEYS", begin_secret)
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        response_text=f"Worker echoed {active_secret}",
    )

    class EntryRotatingSource(FakeAdapterSource):
        @asynccontextmanager
        async def open_attempt(self, start):
            self.opens += 1
            self.starts.append(start)
            monkeypatch.setenv("UNIGROK_API_KEYS", active_secret)
            try:
                yield self.adapter
            finally:
                self.closes += 1
                monkeypatch.setenv("UNIGROK_API_KEYS", closed_secret)

    source = EntryRotatingSource(adapter)
    db_path = tmp_path / "broker-entry-rotation.db"
    store = GrokSessionStore(db_path)
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)

    first = await broker.execute(plan)
    replay = await broker.execute(plan)
    async with store._conn.execute(
        "SELECT output_text, completion_json FROM provider_attempts"
    ) as cursor:
        raw_row = await cursor.fetchone()

    assert first.status == replay.status == "returned"
    assert adapter.calls == 1
    assert source.opens == source.closes == 1
    assert first.delegations[0].attempts[0].result == (
        replay.delegations[0].attempts[0].result
    )
    assert active_secret not in first.model_dump_json()
    assert active_secret not in replay.model_dump_json()
    assert active_secret not in str(tuple(raw_row))
    await store.close()

    persisted = b"".join(
        path.read_bytes()
        for path in tmp_path.glob(f"{db_path.name}*")
    )
    assert active_secret.encode() not in persisted


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
async def test_claimed_mcp_request_mutation_is_indeterminate_and_cannot_fallback():
    class ClaimedMutationAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                claim_on_attempt=True,
            )

        async def attempt(self, request):
            self.claim_state = True
            object.__setattr__(
                request.messages[0],
                "content",
                "forged physical prompt after sampling claim",
            )
            return await super().attempt(request)

    subscription = ClaimedMutationAdapter()
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan())

    failure = result.delegations[0].attempts[0].result.failure
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert api.calls == 0


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
    store.canonical_result_digests = {
        forged_start.attempt_id: provider_broker._provider_result_digest(
            forged_result
        )
    }

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
    projection = store.canonical_provider_attempt_result(
        start.attempt_id, forged_result
    )
    assert await store.complete_projected_provider_attempt(
        start.attempt_id, projection
    )
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
    projection = store.canonical_provider_attempt_result(
        start.attempt_id, forged_result
    )
    assert await store.complete_projected_provider_attempt(
        start.attempt_id, projection
    )
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
async def test_restart_cannot_mint_or_replay_fabricated_terminal_evidence(tmp_path):
    descriptor = _descriptor(ProviderChannel.OPENAI_API)
    plan = _plan(lane_descriptors=(descriptor,))
    start, fabricated = _forged_attempt(
        plan,
        base_descriptor=descriptor,
        forged_descriptor=descriptor,
        ordinal=2,
    )
    db_path = tmp_path / "restart-projection-lease.db"
    first_process = GrokSessionStore(db_path)
    assert await first_process.begin_provider_attempt(start)
    stale_projection = first_process.canonical_provider_attempt_result(
        start.attempt_id,
        fabricated,
    )
    await first_process.close()
    with pytest.raises(ValueError, match="authorization is invalid"):
        await first_process.complete_projected_provider_attempt(
            start.attempt_id,
            stale_projection,
        )

    restarted = GrokSessionStore(db_path)
    with pytest.raises(PermissionError, match="direct provider-attempt completion"):
        await restarted.complete_provider_attempt(start.attempt_id, fabricated)
    with pytest.raises(PermissionError, match="no live canonical-projection lease"):
        restarted.canonical_provider_attempt_result(start.attempt_id, fabricated)

    adapter = FakeAdapter(ProviderChannel.OPENAI_API, descriptor=descriptor)
    replay = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: adapter},
        store=restarted,
        clock=lambda: NOW,
    ).execute(plan)

    assert replay.status == "indeterminate"
    assert adapter.calls == 0
    assert "forged but internally" not in replay.model_dump_json()
    assert (await restarted.list_provider_attempts())[0][
        "transport_status"
    ] == "started"
    await restarted.close()


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
    projection = store.canonical_provider_attempt_result(
        start.attempt_id, forged_result
    )
    assert await store.complete_projected_provider_attempt(
        start.attempt_id, projection
    )
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
    subscription_projection = store.canonical_provider_attempt_result(
        subscription_start.attempt_id,
        internal_failure,
    )
    assert await store.complete_projected_provider_attempt(
        subscription_start.attempt_id,
        subscription_projection,
    )
    assert await store.begin_provider_attempt(api_start)
    api_projection = store.canonical_provider_attempt_result(
        api_start.attempt_id, api_result
    )
    assert await store.complete_projected_provider_attempt(
        api_start.attempt_id, api_projection
    )

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
async def test_claimed_mcp_late_result_is_internal_indeterminate():
    clock_now = [NOW]

    class ClaimedLateAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                claim_on_attempt=True,
            )

        async def attempt(self, request):
            result = await super().attempt(request)
            clock_now[0] = NOW + timedelta(seconds=2)
            return result

    subscription = ClaimedLateAdapter()
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: clock_now[0],
    ).execute(_plan(ttl=NOW + timedelta(seconds=1)))

    failure = result.delegations[0].attempts[0].result.failure
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert api.calls == 0


@pytest.mark.asyncio
async def test_adapter_owned_result_mutation_after_return_cannot_enable_fallback():
    mutation_done = asyncio.Event()

    class DeferredMutationAdapter(FakeAdapter):
        async def attempt(self, request):
            result = await super().attempt(request)
            failure = ProviderFailureReceipt(
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
                error_kind="transport",
                error_code="timeout",
                duration_ms=2,
            )

            def mutate_returned_object():
                object.__setattr__(result, "status", "failed")
                object.__setattr__(result, "response", None)
                object.__setattr__(result, "failure", failure)
                mutation_done.set()

            asyncio.get_running_loop().call_soon(mutate_returned_object)
            return result

    subscription = DeferredMutationAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        claim_on_attempt=True,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(_plan())

    await asyncio.wait_for(mutation_done.wait(), timeout=1)
    assert subscription.claim_state is True
    assert subscription.calls == 1
    assert api.calls == 0
    assert result.status == "returned"
    terminal = next(iter(store.results.values()))
    assert terminal.status == "returned"


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
async def test_claimed_mcp_cancellation_persists_internal_and_replay_cannot_fallback():
    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10.0,
        claim_on_attempt=True,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    broker = GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan()
    task = asyncio.create_task(broker.execute(plan))
    async with asyncio.timeout(1.0):
        while subscription.calls == 0:
            await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    terminal = next(iter(store.results.values()))
    assert terminal.failure.error_kind == "internal"
    assert terminal.failure.error_code == "sampling_effect_indeterminate"
    replay = await broker.execute(plan)
    replay_evidence = replay.delegations[0].attempts[0]
    assert replay.status == "failed"
    assert replay_evidence.persistence == "replayed_terminal"
    assert replay_evidence.result == terminal
    assert subscription.calls == 1
    assert api.calls == 0


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
async def test_repeated_cancellation_cannot_bypass_dynamic_source_terminal_write():
    events: list[str] = []
    adapter = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        delay=10,
        events=events,
    )
    source = FakeAdapterSource(adapter, events=events)
    store = BlockingCompleteStore(events=events)
    task = asyncio.create_task(
        GrokWorkerBroker(
            registry={ProviderChannel.OPENAI_MCP_SAMPLING: source},
            store=store,
            clock=lambda: NOW,
        ).execute(_plan(fallback=False))
    )
    async with asyncio.timeout(1):
        while adapter.calls == 0:
            await asyncio.sleep(0)

    task.cancel()
    await asyncio.wait_for(store.complete_entered.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert store.results == {}

    store.complete_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert source.closes == 1
    assert len(store.results) == 1
    assert events.index("close:openai_mcp_sampling") < events.index(
        "complete:openai_mcp_sampling"
    )
    terminal = next(iter(store.results.values()))
    assert terminal.status == "failed"
    assert terminal.failure.error_code == "broker_cancelled"


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
async def test_blocking_terminal_replay_cannot_overrun_absolute_bound():
    class BlockingReplayStore(FakeStore):
        async def list_provider_attempts(self, *args, **kwargs):
            if self.results:
                time.sleep(0.5)
            return await super().list_provider_attempts(*args, **kwargs)

    store = BlockingReplayStore()
    started = time.monotonic()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING
            )
        },
        store=store,
        clock=lambda: NOW,
        terminal_write_timeout_seconds=0.1,
    ).execute(_plan(fallback=False))

    evidence = result.delegations[0].attempts[0]
    assert time.monotonic() - started >= 0.5
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.reason == "terminal_persistence_failed"
    assert len(store.results) == 1


@pytest.mark.asyncio
async def test_terminal_replay_must_equal_predeclared_canonical_projection():
    class DifferentReplayStore(FakeStore):
        async def list_provider_attempts(self, *args, **kwargs):
            rows = await super().list_provider_attempts(*args, **kwargs)
            if self.results:
                for row in rows:
                    if row.get("transport_status") == "returned":
                        row["output_text"] = "different contract-valid response"
            return rows

    store = DifferentReplayStore()
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    broker = GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: adapter
        },
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)
    result = await broker.execute(plan)
    replay = await broker.execute(plan)

    evidence = result.delegations[0].attempts[0]
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert evidence.harvest.reason == "terminal_persistence_failed"
    assert "different contract-valid response" not in result.model_dump_json()
    assert replay.status == "indeterminate"
    assert replay.delegations[0].reason == "stored_attempt_conflict"
    assert "different contract-valid response" not in replay.model_dump_json()
    assert adapter.calls == 1
    assert len(store.results) == 1


@pytest.mark.asyncio
async def test_complete_cannot_substitute_broker_approved_canonical_result():
    class DifferentCompleteStore(FakeStore):
        async def complete_projected_provider_attempt(
            self,
            attempt_id,
            projection,
        ):
            substituted_result = projection.result.model_copy(
                update={
                    "response": projection.result.response.model_copy(
                        update={"text": "different complete result"}
                    )
                }
            )
            substituted = projection.model_copy(
                update={"result": substituted_result}
            )
            return await super().complete_projected_provider_attempt(
                attempt_id,
                substituted,
            )

    store = DifferentCompleteStore()
    adapter = FakeAdapter(ProviderChannel.OPENAI_MCP_SAMPLING)
    broker = GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_MCP_SAMPLING: adapter},
        store=store,
        clock=lambda: NOW,
    )
    plan = _plan(fallback=False)
    first = await broker.execute(plan)
    second = await broker.execute(plan)

    assert first.status == second.status == "indeterminate"
    assert first.delegations[0].attempts[0].persistence == (
        "terminal_indeterminate"
    )
    assert second.delegations[0].reason == "stored_attempt_indeterminate"
    assert "different complete result" not in first.model_dump_json()
    assert "different complete result" not in second.model_dump_json()
    assert adapter.calls == 1
    assert store.results == {}


@pytest.mark.asyncio
async def test_canonical_projection_may_only_transform_response_text():
    class AuthorityChangingProjectionStore(FakeStore):
        def canonical_provider_attempt_result(
            self,
            attempt_id,
            result,
            redaction_snapshot=None,
        ):
            projection = super().canonical_provider_attempt_result(
                attempt_id,
                result,
                redaction_snapshot,
            )
            changed = projection.result.model_copy(
                update={
                    "response": projection.result.response.model_copy(
                        update={"model": "different-model"}
                    )
                }
            )
            return projection.model_copy(update={"result": changed})

    store = AuthorityChangingProjectionStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: FakeAdapter(
                ProviderChannel.OPENAI_MCP_SAMPLING
            )
        },
        store=store,
        clock=lambda: NOW,
    ).execute(_plan(fallback=False))

    evidence = result.delegations[0].attempts[0]
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert store.results == {}


@pytest.mark.asyncio
async def test_claimed_mcp_failure_rejects_projection_mutation_and_never_falls_back():
    class InPlaceMutationStore(FakeStore):
        projection_calls = 0

        def canonical_provider_attempt_result(
            self,
            attempt_id,
            result,
            redaction_snapshot=None,
        ):
            self.projection_calls += 1
            object.__setattr__(result.failure, "error_kind", "transport")
            object.__setattr__(result.failure, "error_code", "timeout")
            return super().canonical_provider_attempt_result(
                attempt_id,
                result,
                redaction_snapshot,
            )

    subscription = FakeAdapter(
        ProviderChannel.OPENAI_MCP_SAMPLING,
        outcome="failed",
        claim_on_attempt=True,
    )
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = InPlaceMutationStore()
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(_plan())

    evidence = result.delegations[0].attempts[0]
    assert subscription.claim_state is True
    assert subscription.calls == 1
    assert api.calls == 0
    assert store.projection_calls == 1
    assert result.status == "indeterminate"
    assert evidence.persistence == "terminal_indeterminate"
    assert evidence.result is None
    assert store.results == {}


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
async def test_known_claim_before_dispatch_blocks_descriptor_failure_fallback():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(update={"endpoint_host": "forged.openai.com"})

    class ClaimedBeforeDispatchAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                descriptor=authorized,
                claim_state=True,
            )
            self.descriptor_reads = 0

        @property
        def descriptor(self):
            self.descriptor_reads += 1
            return changed if self.descriptor_reads >= 4 else authorized

    subscription = ClaimedBeforeDispatchAdapter()
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    store = FakeStore()
    plan = _plan(lane_descriptors=(authorized, api.descriptor))
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=store,
        clock=lambda: NOW,
    ).execute(plan)

    attempt = result.delegations[0].attempts[0]
    assert result.status == "failed"
    assert attempt.persistence == "durable_terminal"
    assert attempt.result.failure.error_kind == "internal"
    assert attempt.result.failure.error_code == "sampling_effect_indeterminate"
    assert subscription.calls == 0
    assert api.calls == 0


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
            await asyncio.sleep(0.01)
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
    assert attempt.persistence == "durable_terminal"
    assert attempt.result.status == "failed"
    assert attempt.result.failure.error_code == "descriptor_authorization_changed"
    assert "return that must not be certified" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_claimed_mcp_descriptor_change_is_internal_and_cannot_fallback():
    authorized = _descriptor(ProviderChannel.OPENAI_MCP_SAMPLING)
    changed = authorized.model_copy(
        update={"models": authorized.models.model_copy(update={"planning": "gpt-forged"})}
    )

    class ClaimedDescriptorSwitchAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                ProviderChannel.OPENAI_MCP_SAMPLING,
                descriptor=authorized,
                claim_on_attempt=True,
            )
            self.switched = False

        @property
        def descriptor(self):
            return changed if self.switched else authorized

        async def attempt(self, request):
            self.claim_state = True
            self.switched = True
            await asyncio.sleep(0.01)
            receipt = _receipt(request, authorized)
            return ProviderAttemptResult(
                status="returned",
                response=ProviderResponse(
                    provider=authorized.provider,
                    channel=authorized.channel,
                    model=receipt.resolved_model,
                    text="claimed return under a changed descriptor",
                    finish_reason="stop",
                    receipt=receipt,
                ),
            )

    subscription = ClaimedDescriptorSwitchAdapter()
    api = FakeAdapter(ProviderChannel.OPENAI_API)
    result = await GrokWorkerBroker(
        registry={
            ProviderChannel.OPENAI_MCP_SAMPLING: subscription,
            ProviderChannel.OPENAI_API: api,
        },
        store=FakeStore(),
        clock=lambda: NOW,
    ).execute(_plan(lane_descriptors=(authorized, api.descriptor)))

    attempt = result.delegations[0].attempts[0]
    assert attempt.persistence == "durable_terminal"
    failure = attempt.result.failure
    assert failure.error_kind == "internal"
    assert failure.error_code == "sampling_effect_indeterminate"
    assert api.calls == 0


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
