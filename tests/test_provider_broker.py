from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
import time

import pytest
from pydantic import ValidationError

from src.providers import (
    BrokerHarvestStatus,
    BrokerCancellationPersistenceError,
    CredentialPlane,
    CredentialState,
    GrokDelegationPlan,
    GrokSupervisorBinding,
    GrokWorkerBroker,
    GrokWorkerDelegation,
    ProviderAttemptResult,
    ProviderChannel,
    ProviderDescriptor,
    ProviderFailureReceipt,
    ProviderId,
    ProviderMessage,
    ProviderModelPins,
    ProviderReceipt,
    ProviderResponse,
    ProviderTokenUsage,
    RouteClass,
    WorkerFallbackPolicy,
)
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
    ) -> None:
        self._descriptor = descriptor or _descriptor(channel)
        self.outcome = outcome
        self.failure_kind = failure_kind
        self.delay = delay
        self.events = events if events is not None else []
        self.tracker = tracker
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
                    text=f"evidence from {self.descriptor.channel.value}",
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
        region="test",
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
            result = self.results.get(attempt_id)
            if result is None:
                rows.append(
                    {
                        "attempt_id": attempt_id,
                        "transport_status": "started",
                    }
                )
                continue
            receipt = result.response.receipt if result.response else result.failure
            rows.append(
                {
                    "attempt_id": attempt_id,
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

    async def run_once(self, store):
        self.calls += 1
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
) -> GrokDelegationPlan:
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
                timeout_seconds=timeout_seconds,
            )
            for index in range(count)
        ),
        max_concurrency=max_concurrency,
    )


@pytest.mark.asyncio
async def test_plan_is_strict_content_addressed_and_construction_is_inert():
    plan = _plan()
    same = _plan()
    changed = _plan(provider=ProviderId.ANTHROPIC)
    assert plan.plan_id == same.plan_id
    assert plan.plan_digest == same.plan_digest
    assert plan.plan_digest != changed.plan_digest

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
        )

    raw = plan.model_dump(mode="python")
    raw["delegations"][0]["channel"] = "openai_api"
    with pytest.raises(ValidationError, match="Extra inputs"):
        GrokDelegationPlan.model_validate(raw)


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
async def test_identity_ignores_display_state_drift_but_endpoint_drift_fails_closed():
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
    refused = await GrokWorkerBroker(
        registry={ProviderChannel.OPENAI_API: changed_adapter},
        store=store,
        clock=lambda: NOW,
    ).execute(plan)
    assert refused.status == "indeterminate"
    assert refused.delegations[0].attempts[0].persistence == "begin_failed"
    assert changed_adapter.calls == 0


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
    ).execute(_plan(fallback=False))
    assert result.status == "returned"
    assert adapter.requests[0].max_output_tokens == 1024
    assert adapter.requests[0].timeout_seconds == 3.0


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
    assert "complete" in harvest_states
    assert harvest_states & {"ttl_expired", "timed_out"}


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
