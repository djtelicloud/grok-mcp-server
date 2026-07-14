"""Internal Grok-supervised broker for bounded subordinate model evidence.

This module is deliberately not wired to the public MCP server or routing
stack.  A :class:`GrokDelegationPlan` is a strict transport contract, not proof
that Grok created it.  Only a future trusted Grok runtime may mint and execute
plans at the integration boundary.

The broker chooses physical credential channels from an injected registry,
records every physical attempt before its effect, and returns durable transport
evidence to Grok without synthesizing or granting semantic authority.  It never
constructs credential-bearing transports or a cloud harvester.  It snapshots
server-owned values only for ephemeral result redaction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import contextlib
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import time
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, model_validator

from ..provider_redaction import (
    ProviderRedactionSnapshot,
    capture_provider_redaction_snapshot,
)
from .contracts import (
    MAX_OUTPUT_TOKENS,
    MAX_REQUEST_CHARS,
    MAX_TIMEOUT_SECONDS,
    CredentialPlane,
    CredentialState,
    GrokSupervisorBinding,
    ProviderAdapter,
    ProviderAttemptCanonicalProjection,
    ProviderAttemptResult,
    ProviderAttemptStart,
    ProviderChannel,
    ProviderDescriptor,
    ProviderExecutionBinding,
    ProviderFailureReceipt,
    ProviderId,
    ProviderMessage,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    RouteClass,
    StrictContract,
    WorkerAuthority,
    model_visible_messages,
    provider_result_matches_start,
)


MAX_DELEGATIONS = 32
MAX_BROKER_CONCURRENCY = 8
MAX_PHYSICAL_ATTEMPTS_PER_DELEGATION = 2

_SUBSCRIPTION_LADDERS: dict[ProviderId, tuple[ProviderChannel, ...]] = {
    ProviderId.OPENAI: (ProviderChannel.OPENAI_MCP_SAMPLING,),
    ProviderId.ANTHROPIC: (
        ProviderChannel.ANTHROPIC_MCP_SAMPLING,
        ProviderChannel.CLAUDE_CLI,
    ),
    ProviderId.GOOGLE: (ProviderChannel.GOOGLE_MCP_SAMPLING,),
}
_API_LADDERS: dict[ProviderId, tuple[ProviderChannel, ...]] = {
    ProviderId.OPENAI: (ProviderChannel.OPENAI_API,),
    ProviderId.ANTHROPIC: (ProviderChannel.ANTHROPIC_API,),
    # Vertex is preferred when it is injected and not known-missing.  The
    # policy permits one metered attempt, so Gemini is an alternative lane,
    # never a second metered retry after Vertex.
    ProviderId.GOOGLE: (
        ProviderChannel.VERTEX_ADC,
        ProviderChannel.GEMINI_API_KEY,
    ),
}
_ALL_WORKER_CHANNELS = frozenset(
    channel
    for ladders in (_SUBSCRIPTION_LADDERS, _API_LADDERS)
    for channels in ladders.values()
    for channel in channels
)
_MCP_SAMPLING_CHANNELS = frozenset(
    {
        ProviderChannel.OPENAI_MCP_SAMPLING,
        ProviderChannel.ANTHROPIC_MCP_SAMPLING,
        ProviderChannel.GOOGLE_MCP_SAMPLING,
    }
)
_SAFE_HARVEST_REASON = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class BrokerCancellationPersistenceError(RuntimeError):
    """A cancelled physical attempt could not be durably terminalized."""


class WorkerFallbackPolicy(StrictContract):
    """Bound one delegation to subscription-only or one metered fallback."""

    mode: Literal["subscription_only", "subscription_then_api"]
    max_metered_api_attempts: Literal[0, 1]

    @model_validator(mode="after")
    def validate_limit(self) -> "WorkerFallbackPolicy":
        expected = 1 if self.mode == "subscription_then_api" else 0
        if self.max_metered_api_attempts != expected:
            raise ValueError("fallback mode and metered attempt limit disagree")
        return self


class GrokWorkerLaneAuthorization(StrictContract):
    """Plan-bound authorization for one immutable provider lane snapshot."""

    channel: ProviderChannel
    contract_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_worker_channel(self) -> "GrokWorkerLaneAuthorization":
        if self.channel not in _ALL_WORKER_CHANNELS:
            raise ValueError("lane authorization requires a subordinate channel")
        return self

    @classmethod
    def from_descriptor(
        cls,
        descriptor: ProviderDescriptor,
    ) -> "GrokWorkerLaneAuthorization":
        """Freeze a reviewed descriptor into digest-only plan authority."""

        if descriptor.transport_resource_identity is None:
            raise ValueError(
                "broker lane requires a pinned transport resource identity"
            )
        return cls(
            channel=descriptor.channel,
            contract_digest=_lane_contract_digest(
                provider=descriptor.provider,
                channel=descriptor.channel,
                credential_plane=descriptor.credential_plane,
                execution=_execution_binding(descriptor),
            ),
        )


class GrokWorkerDelegation(StrictContract):
    """One semantic worker request chosen by the Grok supervisor.

    The Grok-owned plan carries only content digests for reviewed physical
    lane snapshots. The broker still applies the fixed same-provider ladder,
    while starts carry the full material needed to verify those digests.
    """

    delegation_key: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
    ]
    provider: Literal[ProviderId.OPENAI, ProviderId.ANTHROPIC, ProviderId.GOOGLE]
    route: RouteClass
    messages: Annotated[
        tuple[ProviderMessage, ...], Field(min_length=1, max_length=100)
    ]
    fallback: WorkerFallbackPolicy
    authorized_lanes: Annotated[
        tuple[GrokWorkerLaneAuthorization, ...],
        Field(min_length=1, max_length=3),
    ]
    max_output_tokens: Annotated[int, Field(ge=1, le=MAX_OUTPUT_TOKENS)] = 4096
    timeout_seconds: Annotated[float, Field(ge=1.0, le=MAX_TIMEOUT_SECONDS)] = 60.0
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None

    @model_validator(mode="after")
    def require_user_message(self) -> "GrokWorkerDelegation":
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("delegation messages require a user turn")
        if sum(len(message.content) for message in self.messages) > MAX_REQUEST_CHARS:
            raise ValueError("combined delegation content exceeds the request bound")
        channels = tuple(lane.channel for lane in self.authorized_lanes)
        if len(set(channels)) != len(channels):
            raise ValueError("authorized lane channels must be unique")
        allowed = _SUBSCRIPTION_LADDERS[self.provider]
        if self.fallback.max_metered_api_attempts == 1:
            allowed = (*allowed, *_API_LADDERS[self.provider])
        if any(channel not in allowed for channel in channels):
            raise ValueError("authorized lane violates provider fallback policy")
        ordered = tuple(channel for channel in allowed if channel in set(channels))
        if channels != ordered:
            raise ValueError("authorized lanes must follow the fixed provider ladder")
        return self


class GrokDelegationPlan(StrictContract):
    """Content-addressed internal plan bound to one exact Grok turn.

    ``supervisor='grok'`` and a Grok-shaped model ID are validation constraints,
    not authentication.  The future runtime integration must accept plans only
    from its trusted Grok session state, never directly from an MCP caller.
    """

    version: Literal["grok-delegation-plan/v2"] = "grok-delegation-plan/v2"
    supervision: GrokSupervisorBinding
    supervisor_plane: Literal["CLI", "API"]
    supervisor_model: Annotated[
        str,
        Field(
            min_length=1,
            max_length=192,
            pattern=r"^grok-[A-Za-z0-9._:/@-]{1,186}$",
        ),
    ]
    delegations: Annotated[
        tuple[GrokWorkerDelegation, ...],
        Field(min_length=1, max_length=MAX_DELEGATIONS),
    ]
    max_concurrency: Annotated[int, Field(ge=1, le=MAX_BROKER_CONCURRENCY)] = 4

    @model_validator(mode="after")
    def validate_delegations(self) -> "GrokDelegationPlan":
        keys = [delegation.delegation_key for delegation in self.delegations]
        if len(set(keys)) != len(keys):
            raise ValueError("delegation keys must be unique")
        if self.supervision.supervisor != "grok":
            raise ValueError("delegation plans require a Grok supervisor")
        return self

    @property
    def plan_digest(self) -> str:
        return _content_digest(_canonical_plan(self))

    @property
    def plan_id(self) -> str:
        return f"gdp:{self.plan_digest.removeprefix('sha256:')}"


def _snapshot_plan(
    plan: GrokDelegationPlan | Mapping[str, Any],
) -> GrokDelegationPlan:
    """Deeply revalidate one plan so caller aliases cannot change execution."""

    validated = (
        plan
        if isinstance(plan, GrokDelegationPlan)
        else GrokDelegationPlan.model_validate(plan)
    )
    return GrokDelegationPlan.model_validate_json(validated.model_dump_json())


class BrokerHarvestStatus(StrictContract):
    status: Literal[
        "complete",
        "partial",
        "idle",
        "unavailable",
        "failed",
        "timed_out",
        "ttl_expired",
        "not_applicable",
    ]
    reason: Annotated[str, Field(pattern=_SAFE_HARVEST_REASON)] | None = None
    leased: Annotated[int, Field(ge=0, le=25)] = 0
    synced: Annotated[int, Field(ge=0, le=25)] = 0
    retry_wait: Annotated[int, Field(ge=0, le=25)] = 0
    lease_lost: Annotated[int, Field(ge=0, le=25)] = 0
    state_errors: Annotated[int, Field(ge=0, le=25)] = 0


class BrokerAttemptEvidence(StrictContract):
    """One physical attempt, with worker output exposed only after durability."""

    start: ProviderAttemptStart
    persistence: Literal[
        "durable_terminal",
        "replayed_terminal",
        "begin_failed",
        "terminal_indeterminate",
        "replay_indeterminate",
    ]
    result: ProviderAttemptResult | None = None
    harvest: BrokerHarvestStatus
    semantic_outcome: Literal["unverified"] = "unverified"
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def validate_durability(self) -> "BrokerAttemptEvidence":
        durable = self.persistence in {"durable_terminal", "replayed_terminal"}
        if durable != (self.result is not None):
            raise ValueError("only durable terminal attempts may expose a result")
        if durable and not provider_result_matches_start(self.start, self.result):
            raise ValueError("durable result does not match its exact attempt start")
        if not durable and self.harvest.status != "not_applicable":
            raise ValueError("non-durable attempts cannot claim a harvest run")
        return self


class BrokerDelegationResult(StrictContract):
    delegation_id: Annotated[str, Field(pattern=r"^dlg:[0-9a-f]{64}$")]
    delegation_key: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    provider: Literal[ProviderId.OPENAI, ProviderId.ANTHROPIC, ProviderId.GOOGLE]
    route: RouteClass
    status: Literal["returned", "failed", "unavailable", "indeterminate", "expired"]
    reason: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    attempts: Annotated[
        tuple[BrokerAttemptEvidence, ...],
        Field(max_length=MAX_PHYSICAL_ATTEMPTS_PER_DELEGATION),
    ] = ()
    semantic_outcome: Literal["unverified"] = "unverified"
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def validate_result(self) -> "BrokerDelegationResult":
        ordinals = [attempt.start.attempt_ordinal for attempt in self.attempts]
        if ordinals not in ([], [1], [2], [1, 2]):
            raise ValueError("attempt ordinals must be unique and in execution order")
        if ordinals == [1, 2]:
            first = self.attempts[0].result
            if (
                first is None
                or first.status != "failed"
                or first.failure is None
                or first.failure.error_kind
                not in {"configuration", "transport", "protocol"}
            ):
                raise ValueError(
                    "API fallback requires an eligible subscription failure"
                )
        for attempt in self.attempts:
            if attempt.start.delegation_id != self.delegation_id:
                raise ValueError("attempt is bound to a different delegation")
            if attempt.start.provider != self.provider:
                raise ValueError("attempt provider does not match its delegation")
            if attempt.start.request.route != self.route:
                raise ValueError("attempt route does not match its delegation")

        returned = [
            attempt
            for attempt in self.attempts
            if attempt.result is not None and attempt.result.status == "returned"
        ]
        if (self.status == "returned") != (len(returned) == 1):
            raise ValueError(
                "returned delegation status must identify one durable return"
            )
        non_durable = [
            attempt
            for attempt in self.attempts
            if attempt.persistence not in {"durable_terminal", "replayed_terminal"}
        ]
        if self.status != "indeterminate" and non_durable:
            raise ValueError(
                "only indeterminate delegations may expose non-durable work"
            )
        if self.status == "indeterminate" and self.attempts and not non_durable:
            raise ValueError("indeterminate status requires persistence evidence")
        if self.status == "failed" and not any(
            attempt.result is not None and attempt.result.status == "failed"
            for attempt in self.attempts
        ):
            raise ValueError("failed delegation status requires a durable failure")
        if self.status == "unavailable" and self.attempts:
            raise ValueError("unavailable delegation cannot contain attempts")
        return self


class GrokWorkerBrokerResult(StrictContract):
    version: Literal["grok-worker-broker-result/v1"] = "grok-worker-broker-result/v1"
    plan_id: Annotated[str, Field(pattern=r"^gdp:[0-9a-f]{64}$")]
    plan_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    supervision: GrokSupervisorBinding
    supervisor_plane: Literal["CLI", "API"]
    supervisor_model: Annotated[
        str,
        Field(
            min_length=1,
            max_length=192,
            pattern=r"^grok-[A-Za-z0-9._:/@-]{1,186}$",
        ),
    ]
    status: Literal["returned", "mixed", "failed", "indeterminate", "expired"]
    delegations: Annotated[
        tuple[BrokerDelegationResult, ...],
        Field(min_length=1, max_length=MAX_DELEGATIONS),
    ]
    semantic_outcome: Literal["unverified"] = "unverified"
    synthesized: Literal[False] = False
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def validate_result(self) -> "GrokWorkerBrokerResult":
        if self.plan_id != f"gdp:{self.plan_digest.removeprefix('sha256:')}":
            raise ValueError("plan_id does not match plan_digest")
        delegation_ids = [item.delegation_id for item in self.delegations]
        delegation_keys = [item.delegation_key for item in self.delegations]
        if len(set(delegation_ids)) != len(delegation_ids):
            raise ValueError("broker result delegation IDs must be unique")
        if len(set(delegation_keys)) != len(delegation_keys):
            raise ValueError("broker result delegation keys must be unique")
        for delegation in self.delegations:
            for attempt in delegation.attempts:
                start = attempt.start
                if start.request.supervision != self.supervision:
                    raise ValueError("attempt supervision does not match broker result")
                if start.supervisor_plane != self.supervisor_plane:
                    raise ValueError(
                        "attempt supervisor plane does not match broker result"
                    )
                if start.supervisor_model != self.supervisor_model:
                    raise ValueError(
                        "attempt supervisor model does not match broker result"
                    )
        if self.status != _global_status(self.delegations):
            raise ValueError("broker status does not match delegation statuses")
        return self

    def validate_against_plan(
        self,
        plan: GrokDelegationPlan | Mapping[str, Any],
    ) -> "GrokWorkerBrokerResult":
        """Bind exported evidence to one exact originating Grok plan.

        The result intentionally does not duplicate model-visible prompts or
        fallback policy. Consumers holding the originating plan must cross the
        same explicit boundary used by :meth:`GrokWorkerBroker.execute` before
        trusting delegation labels or attempt identities.
        """

        validated = _snapshot_plan(plan)
        if (
            self.plan_id != validated.plan_id
            or self.plan_digest != validated.plan_digest
            or self.supervision != validated.supervision
            or self.supervisor_plane != validated.supervisor_plane
            or self.supervisor_model != validated.supervisor_model
            or len(self.delegations) != len(validated.delegations)
        ):
            raise ValueError("broker result does not match its originating plan")
        for index, (result, delegation) in enumerate(
            zip(self.delegations, validated.delegations, strict=True)
        ):
            if (
                result.delegation_id != _delegation_id(validated, index)
                or result.delegation_key != delegation.delegation_key
                or result.provider != delegation.provider
                or result.route != delegation.route
            ):
                raise ValueError("broker delegation does not match its plan entry")
            if not all(
                _attempt_start_matches_plan(attempt.start, validated, index)
                for attempt in result.attempts
            ):
                raise ValueError("broker attempt does not match its plan delegation")
        return self


class ProviderAttemptStore(Protocol):
    async def begin_provider_attempt(self, start: Any) -> bool: ...

    def canonical_provider_attempt_result(
        self,
        attempt_id: str,
        result: Any,
        redaction_snapshot: ProviderRedactionSnapshot | None = None,
    ) -> ProviderAttemptCanonicalProjection: ...

    async def revoke_provider_attempt_projection(self, attempt_id: str) -> None: ...

    async def complete_projected_provider_attempt(
        self,
        attempt_id: str,
        projection: ProviderAttemptCanonicalProjection,
    ) -> bool: ...

    async def list_provider_attempts(
        self,
        supervisor_session_id: str | None = None,
        delegation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


class ProviderAttemptHarvestTrigger(Protocol):
    async def run_once(self, store: Any, *, deadline_monotonic: float) -> Any: ...


class ProviderAttemptAdapterSource(Protocol):
    """Stable lane metadata that opens an adapter for one durable attempt.

    ``open_attempt`` is called only after the exact attempt start is durably
    recorded and must synchronously return an otherwise inert context manager.
    Entering and exiting that context may acquire and revoke local request
    authority, but must not invoke the provider effect; the yielded adapter's
    ``attempt`` method remains the sole physical effect boundary.  Its
    ``__aexit__`` must be safe and idempotent after a failed or cancelled
    ``__aenter__`` so the broker can mechanically revoke partially acquired
    authority before recording any terminal result.
    """

    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def open_attempt(
        self,
        start: ProviderAttemptStart,
    ) -> AbstractAsyncContextManager[ProviderAdapter]: ...


class _StaticProviderAttemptAdapterSource:
    """Compatibility source for existing process- and API-scoped adapters."""

    def __init__(self, adapter: ProviderAdapter) -> None:
        self._adapter = adapter

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._adapter.descriptor

    @property
    def adapter(self) -> ProviderAdapter:
        return self._adapter

    @asynccontextmanager
    async def open_attempt(self, start: ProviderAttemptStart):
        del start
        yield self._adapter


@dataclass(frozen=True, slots=True)
class _PreparedTerminalResult:
    authoritative_digest: str
    projection: ProviderAttemptCanonicalProjection


@dataclass(frozen=True, slots=True)
class _TerminalProjectionWork:
    attempt_id: str
    authoritative_digest: str
    task: asyncio.Task[tuple[float, Any]]


@dataclass(frozen=True, slots=True)
class _AdapterLifecycleOutcome:
    result: ProviderAttemptResult | None
    prepared: _PreparedTerminalResult | None = None
    cancelled: bool = False
    indeterminate_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _AdapterCleanupOutcome:
    status: Literal["complete", "failed", "timed_out"]


@dataclass(frozen=True, slots=True)
class _TerminalPersistenceOutcome:
    result: ProviderAttemptResult | None
    stored: bool = False
    error: BaseException | None = None


def _canonical_plan(plan: GrokDelegationPlan) -> str:
    payload = plan.model_dump(mode="json")
    payload["supervision"]["ttl_expires_at"] = (
        plan.supervision.ttl_expires_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _content_digest(value: str) -> str:
    return (
        "sha256:" + hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()
    )


def _provider_result_digest(result: ProviderAttemptResult) -> str:
    return _content_digest(
        json.dumps(
            result.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    )


def _snapshot_provider_result(result: ProviderAttemptResult) -> ProviderAttemptResult:
    return ProviderAttemptResult.model_validate_json(
        result.model_dump_json(warnings="error")
    )


def _stable_id(prefix: str, *parts: str) -> str:
    body = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return f"{prefix}:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"


def _delegation_id(plan: GrokDelegationPlan, index: int) -> str:
    delegation = plan.delegations[index]
    canonical = json.dumps(
        delegation.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return _stable_id("dlg", plan.plan_digest, str(index), canonical)


def _attempt_start_matches_plan(
    start: ProviderAttemptStart,
    plan: GrokDelegationPlan,
    index: int,
) -> bool:
    delegation = plan.delegations[index]
    if start.attempt_ordinal == 1:
        expected_channels = _SUBSCRIPTION_LADDERS[delegation.provider]
        expected_plane = CredentialPlane.SUBSCRIPTION
    elif start.attempt_ordinal == 2:
        if delegation.fallback.max_metered_api_attempts != 1:
            return False
        expected_channels = _API_LADDERS[delegation.provider]
        expected_plane = CredentialPlane.METERED_API
    else:
        return False
    execution = start.execution
    if (
        start.version != "provider-attempt-start/v3"
        or execution is None
        or not execution.has_lane_material
    ):
        return False
    authorization = next(
        (lane for lane in delegation.authorized_lanes if lane.channel == start.channel),
        None,
    )
    if authorization is None:
        return False
    lane_contract_digest = _lane_contract_digest(
        provider=start.provider,
        channel=start.channel,
        credential_plane=start.credential_plane,
        execution=execution,
    )
    if authorization.contract_digest != lane_contract_digest:
        return False
    if delegation.route not in execution.supported_routes:
        return False
    expected_model = execution.model_for_route(delegation.route)
    expected_output_tokens = min(
        delegation.max_output_tokens,
        execution.max_output_tokens,
    )
    expected_timeout_seconds = min(
        delegation.timeout_seconds,
        execution.max_timeout_seconds,
    )
    execution_digest = _execution_contract_digest(
        provider=delegation.provider,
        channel=start.channel,
        credential_plane=expected_plane,
        execution=execution,
        route=delegation.route,
        requested_model=expected_model,
        max_output_tokens=expected_output_tokens,
        timeout_seconds=expected_timeout_seconds,
    )
    expected_attempt_id = _stable_id(
        "att",
        plan.plan_digest,
        start.delegation_id,
        start.channel.value,
        str(start.attempt_ordinal),
        expected_model,
        execution_digest,
    )
    expected_request_id = _stable_id(
        "req",
        plan.plan_digest,
        start.delegation_id,
        expected_attempt_id,
        start.channel.value,
        expected_model,
        execution_digest,
    )
    return all(
        (
            start.delegation_id == _delegation_id(plan, index),
            start.provider == delegation.provider,
            start.channel in expected_channels,
            start.credential_plane == expected_plane,
            start.supervisor_plane == plan.supervisor_plane,
            start.supervisor_model == plan.supervisor_model,
            start.request.supervision == plan.supervision,
            start.request.route == delegation.route,
            tuple(start.request.messages) == delegation.messages,
            start.requested_model == expected_model,
            start.request.model == expected_model,
            start.request.max_output_tokens == expected_output_tokens,
            start.request.timeout_seconds == expected_timeout_seconds,
            start.request.temperature == delegation.temperature,
            start.attempt_id == expected_attempt_id,
            start.request.request_id == expected_request_id,
        )
    )


def _execution_binding(descriptor: ProviderDescriptor) -> ProviderExecutionBinding:
    return ProviderExecutionBinding(
        endpoint_host=descriptor.endpoint_host,
        endpoint_kind=descriptor.endpoint_kind,
        credential_kind=descriptor.credential_kind,
        billing_class=descriptor.billing_class,
        client_identity=descriptor.client_identity,
        transport_resource_identity=descriptor.transport_resource_identity,
        data_handling=descriptor.data_handling,
        residency=descriptor.residency,
        supports_normalized_tools=descriptor.supports_normalized_tools,
        planning_model=descriptor.models.planning,
        coding_model=descriptor.models.coding,
        vision_model=descriptor.models.vision,
        research_model=descriptor.models.research,
        supported_routes=descriptor.supported_routes,
        max_output_tokens=descriptor.max_output_tokens,
        max_timeout_seconds=descriptor.max_timeout_seconds,
    )


def _lane_contract_digest(
    *,
    provider: ProviderId,
    channel: ProviderChannel,
    credential_plane: CredentialPlane,
    execution: ProviderExecutionBinding,
) -> str:
    if not execution.has_lane_material:
        raise ValueError("lane contract digest requires complete lane material")
    payload = {
        "provider": provider.value,
        "channel": channel.value,
        "credential_plane": credential_plane.value,
        "execution": execution.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return _content_digest(canonical)


def _execution_contract_digest(
    *,
    provider: ProviderId,
    channel: ProviderChannel,
    credential_plane: CredentialPlane,
    execution: ProviderExecutionBinding,
    route: RouteClass,
    requested_model: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> str:
    """Hash effect semantics while excluding mutable availability metadata."""

    payload = {
        "lane_contract_digest": _lane_contract_digest(
            provider=provider,
            channel=channel,
            credential_plane=credential_plane,
            execution=execution,
        ),
        "route": route.value,
        "requested_model": requested_model,
        "max_output_tokens": max_output_tokens,
        "timeout_seconds": timeout_seconds,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return _content_digest(canonical)


def _global_status(results: tuple[BrokerDelegationResult, ...]) -> str:
    statuses = {result.status for result in results}
    if "indeterminate" in statuses:
        return "indeterminate"
    if statuses == {"returned"}:
        return "returned"
    if statuses == {"expired"}:
        return "expired"
    if statuses <= {"failed", "unavailable"}:
        return "failed"
    return "mixed"


class GrokWorkerBroker:
    """Execute strict subordinate attempts while preserving Grok authority."""

    def __init__(
        self,
        *,
        registry: Mapping[
            ProviderChannel,
            ProviderAdapter | ProviderAttemptAdapterSource,
        ],
        store: ProviderAttemptStore,
        harvester: ProviderAttemptHarvestTrigger | None = None,
        clock: Any | None = None,
        harvest_timeout_seconds: float = 5.0,
        terminal_write_timeout_seconds: float = 2.0,
        adapter_cleanup_timeout_seconds: float = 2.0,
    ) -> None:
        if not 0.1 <= float(harvest_timeout_seconds) <= 30.0:
            raise ValueError("harvest trigger timeout is out of bounds")
        if not 0.1 <= float(terminal_write_timeout_seconds) <= 5.0:
            raise ValueError("terminal write timeout is out of bounds")
        if not 0.1 <= float(adapter_cleanup_timeout_seconds) <= 5.0:
            raise ValueError("adapter cleanup timeout is out of bounds")
        if any(channel not in _ALL_WORKER_CHANNELS for channel in registry):
            raise ValueError("broker registry contains a supervisor channel")
        self._registry = {
            channel: self._coerce_adapter_source(entry)
            for channel, entry in registry.items()
        }
        self._store = store
        self._harvester = harvester
        self._clock = clock or (lambda: datetime.now(UTC))
        self._harvest_timeout_seconds = float(harvest_timeout_seconds)
        self._terminal_write_timeout_seconds = float(terminal_write_timeout_seconds)
        self._adapter_cleanup_timeout_seconds = float(
            adapter_cleanup_timeout_seconds
        )
        self._harvest_lock = asyncio.Lock()
        self._validate_registry()

    @staticmethod
    def _coerce_adapter_source(
        entry: ProviderAdapter | ProviderAttemptAdapterSource,
    ) -> ProviderAttemptAdapterSource:
        has_open = callable(getattr(entry, "open_attempt", None))
        has_attempt = callable(getattr(entry, "attempt", None))
        has_complete = callable(getattr(entry, "complete", None))
        if has_open and not has_attempt and not has_complete:
            return entry
        if not has_open and has_attempt and has_complete:
            return _StaticProviderAttemptAdapterSource(entry)
        raise ValueError(
            "broker registry entry must be exactly one adapter or adapter source"
        )

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("broker clock must return a timezone-aware datetime")
        return now

    def _remaining(self, plan: GrokDelegationPlan) -> float:
        return (plan.supervision.ttl_expires_at - self._now()).total_seconds()

    def _validate_registry(self) -> None:
        descriptor_channels: set[ProviderChannel] = set()
        for channel, source in self._registry.items():
            if channel not in _ALL_WORKER_CHANNELS:
                raise ValueError("broker registry contains a supervisor channel")
            descriptor = source.descriptor
            if descriptor.channel != channel:
                raise ValueError("registry key and adapter channel do not match")
            if descriptor.channel in descriptor_channels:
                raise ValueError(
                    "broker registry contains a duplicate physical channel"
                )
            descriptor_channels.add(descriptor.channel)
            expected_provider = next(
                provider
                for provider in _SUBSCRIPTION_LADDERS
                if channel
                in (*_SUBSCRIPTION_LADDERS[provider], *_API_LADDERS[provider])
            )
            if descriptor.provider != expected_provider:
                raise ValueError("registry channel and provider do not match")
            expected_plane = (
                CredentialPlane.SUBSCRIPTION
                if channel in _SUBSCRIPTION_LADDERS[expected_provider]
                else CredentialPlane.METERED_API
            )
            if descriptor.credential_plane != expected_plane:
                raise ValueError("registry channel and credential plane do not match")

    @staticmethod
    def _snapshot_descriptor(descriptor: ProviderDescriptor) -> ProviderDescriptor:
        """Detach trusted effect material from adapter-owned object identity."""

        return ProviderDescriptor.model_validate_json(descriptor.model_dump_json())

    @staticmethod
    def _descriptor_is_authorized(
        *,
        delegation: GrokWorkerDelegation,
        channel: ProviderChannel,
        plane: CredentialPlane,
        descriptor: ProviderDescriptor,
    ) -> bool:
        authorization = next(
            (lane for lane in delegation.authorized_lanes if lane.channel == channel),
            None,
        )
        if authorization is None:
            return False
        try:
            return all(
                (
                    descriptor.channel == channel,
                    descriptor.provider == delegation.provider,
                    descriptor.credential_plane == plane,
                    authorization.contract_digest
                    == _lane_contract_digest(
                        provider=descriptor.provider,
                        channel=descriptor.channel,
                        credential_plane=descriptor.credential_plane,
                        execution=_execution_binding(descriptor),
                    ),
                )
            )
        except Exception:
            return False

    def _descriptor_matches_start(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
    ) -> bool:
        delegation = plan.delegations[index]
        return all(
            (
                self._descriptor_is_authorized(
                    delegation=delegation,
                    channel=start.channel,
                    plane=start.credential_plane,
                    descriptor=descriptor,
                ),
                _execution_binding(descriptor) == start.execution,
                descriptor.credential_state != CredentialState.MISSING,
                delegation.route in descriptor.supported_routes,
            )
        )

    def _select_channel(
        self,
        *,
        delegation: GrokWorkerDelegation,
        plane: CredentialPlane,
    ) -> tuple[
        ProviderChannel,
        ProviderAttemptAdapterSource,
        ProviderDescriptor,
    ] | None:
        ladder = (
            _SUBSCRIPTION_LADDERS[delegation.provider]
            if plane == CredentialPlane.SUBSCRIPTION
            else _API_LADDERS[delegation.provider]
        )
        for channel in ladder:
            if not any(lane.channel == channel for lane in delegation.authorized_lanes):
                continue
            source = self._registry.get(channel)
            if source is None:
                continue
            descriptor = self._snapshot_descriptor(source.descriptor)
            if not self._descriptor_is_authorized(
                delegation=delegation,
                channel=channel,
                plane=plane,
                descriptor=descriptor,
            ):
                raise ValueError("live descriptor is not authorized by the Grok plan")
            if descriptor.credential_state == CredentialState.MISSING:
                continue
            if delegation.route not in descriptor.supported_routes:
                continue
            return channel, source, descriptor
        return None

    def _start(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        channel: ProviderChannel,
        descriptor: ProviderDescriptor,
        ordinal: Literal[1, 2],
    ) -> ProviderAttemptStart:
        delegation = plan.delegations[index]
        delegation_id = _delegation_id(plan, index)
        execution = _execution_binding(descriptor)
        requested_model = execution.model_for_route(delegation.route)
        effective_output_tokens = min(
            delegation.max_output_tokens,
            execution.max_output_tokens,
        )
        effective_timeout_seconds = min(
            delegation.timeout_seconds,
            execution.max_timeout_seconds,
        )
        authorization = next(
            (
                lane
                for lane in delegation.authorized_lanes
                if lane.channel == descriptor.channel
            ),
            None,
        )
        if authorization is None or authorization.contract_digest != (
            _lane_contract_digest(
                provider=descriptor.provider,
                channel=descriptor.channel,
                credential_plane=descriptor.credential_plane,
                execution=execution,
            )
        ):
            raise ValueError("descriptor is not authorized by the Grok plan")
        descriptor_digest = _execution_contract_digest(
            provider=descriptor.provider,
            channel=descriptor.channel,
            credential_plane=descriptor.credential_plane,
            execution=execution,
            route=delegation.route,
            requested_model=requested_model,
            max_output_tokens=effective_output_tokens,
            timeout_seconds=effective_timeout_seconds,
        )
        attempt_id = _stable_id(
            "att",
            plan.plan_digest,
            delegation_id,
            channel.value,
            str(ordinal),
            requested_model,
            descriptor_digest,
        )
        request_id = _stable_id(
            "req",
            plan.plan_digest,
            delegation_id,
            attempt_id,
            channel.value,
            requested_model,
            descriptor_digest,
        )
        request = ProviderRequest(
            request_id=request_id,
            supervision=plan.supervision,
            route=delegation.route,
            messages=delegation.messages,
            model=requested_model,
            max_output_tokens=effective_output_tokens,
            timeout_seconds=effective_timeout_seconds,
            temperature=delegation.temperature,
        )
        return ProviderAttemptStart(
            version="provider-attempt-start/v3",
            attempt_id=attempt_id,
            delegation_id=delegation_id,
            attempt_ordinal=ordinal,
            supervisor_plane=plan.supervisor_plane,
            supervisor_model=plan.supervisor_model,
            provider=delegation.provider,
            channel=channel,
            credential_plane=descriptor.credential_plane,
            requested_model=requested_model,
            execution=execution,
            request=request,
        )

    @staticmethod
    def _normalized_failure(
        *,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
        error_kind: Literal["configuration", "transport", "protocol", "internal"],
        error_code: str,
        duration_ms: int,
    ) -> ProviderAttemptResult:
        return ProviderAttemptResult(
            status="failed",
            failure=ProviderFailureReceipt(
                request_id=start.request.request_id,
                supervision=start.request.supervision,
                provider=start.provider,
                channel=start.channel,
                credential_plane=start.credential_plane,
                route=start.request.route,
                requested_model=start.requested_model,
                endpoint_host=descriptor.endpoint_host,
                endpoint_kind=descriptor.endpoint_kind,
                credential_kind=descriptor.credential_kind,
                billing_class=descriptor.billing_class,
                client_identity=descriptor.client_identity,
                error_kind=error_kind,
                error_code=error_code,
                duration_ms=max(0, min(duration_ms, 3_600_000)),
            ),
        )

    @staticmethod
    def _sampling_effect_requires_indeterminate(
        *,
        channel: ProviderChannel,
        adapter: ProviderAdapter,
    ) -> bool:
        """Fail closed when an MCP sampling effect may already exist.

        Only the typed MCP sampling channels carry this one-shot contract.
        Missing, throwing, or non-boolean inspectors cannot prove that the
        physical callback remained unclaimed, so the broker must forbid a
        metered retry or a first dispatch under that durable attempt.
        """

        if channel not in _MCP_SAMPLING_CHANNELS:
            return False
        try:
            inspector = getattr(adapter, "effect_claimed")
            if not callable(inspector):
                return True
            claimed = inspector()
        except BaseException:
            return True
        return claimed if type(claimed) is bool else True

    def _pre_dispatch_failure(
        self,
        *,
        adapter: ProviderAdapter,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
        error_kind: Literal["configuration", "transport", "protocol", "internal"],
        error_code: str,
        duration_ms: int,
    ) -> ProviderAttemptResult:
        """Forbid retry when the exact sampling grant was already consumed.

        Once the start is durable, a positive or unreadable one-shot state
        must never be repeated through this adapter or an API fallback.
        """

        if self._sampling_effect_requires_indeterminate(
            channel=start.channel,
            adapter=adapter,
        ):
            error_kind = "internal"
            error_code = "sampling_effect_indeterminate"
        return self._normalized_failure(
            start=start,
            descriptor=descriptor,
            error_kind=error_kind,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    def _post_dispatch_failure(
        self,
        *,
        adapter: ProviderAdapter,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
        error_kind: Literal["configuration", "transport", "protocol", "internal"],
        error_code: str,
        duration_ms: int,
    ) -> ProviderAttemptResult:
        if self._sampling_effect_requires_indeterminate(
            channel=start.channel,
            adapter=adapter,
        ):
            error_kind = "internal"
            error_code = "sampling_effect_indeterminate"
        return self._normalized_failure(
            start=start,
            descriptor=descriptor,
            error_kind=error_kind,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _result_matches_start(
        result: ProviderAttemptResult,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
    ) -> bool:
        return all(
            (
                start.execution == _execution_binding(descriptor),
                start.provider == descriptor.provider,
                start.channel == descriptor.channel,
                start.credential_plane == descriptor.credential_plane,
                provider_result_matches_start(start, result),
            )
        )

    async def _invoke_adapter(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        source: ProviderAttemptAdapterSource,
        adapter: ProviderAdapter,
        descriptor: ProviderDescriptor,
        start: ProviderAttemptStart,
        deadline_monotonic: float,
        timeout_code: Literal["ttl_expired", "timeout"],
    ) -> ProviderAttemptResult:
        started = time.monotonic()
        if not _attempt_start_matches_plan(start, plan, index):
            return self._pre_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="start_contract_mismatch",
                duration_ms=0,
            )
        try:
            source_descriptor = self._snapshot_descriptor(source.descriptor)
            current_descriptor = self._snapshot_descriptor(adapter.descriptor)
        except Exception:
            return self._pre_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="descriptor_authorization_changed",
                duration_ms=0,
            )
        if not all(
            self._opened_descriptor_matches_start(
                plan=plan,
                index=index,
                source=source,
                start=start,
                descriptor=descriptor,
                current=candidate,
            )
            for candidate in (source_descriptor, current_descriptor)
        ):
            return self._pre_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="descriptor_authorization_changed",
                duration_ms=0,
            )
        remaining = self._remaining(plan)
        deadline_remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0 or deadline_remaining <= 0:
            return self._pre_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code=("ttl_expired" if remaining <= 0 else timeout_code),
                duration_ms=0,
            )
        physical_timeout = min(remaining, deadline_remaining)
        if self._sampling_effect_requires_indeterminate(
            channel=start.channel,
            adapter=adapter,
        ):
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="internal",
                error_code="sampling_effect_indeterminate",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        # Keep the durable start isolated from adapter-owned code.  Frozen
        # Pydantic models prevent ordinary mutation; the deep copy plus the
        # post-effect equality check also catches adapters that deliberately
        # bypass ``frozen`` through Python object internals.
        physical_request = start.request.model_copy(deep=True)
        try:
            async with asyncio.timeout(physical_timeout):
                raw_result = await adapter.attempt(physical_request)
            wall_remaining = self._remaining(plan)
            deadline_remaining = deadline_monotonic - time.monotonic()
            if wall_remaining <= 0 or deadline_remaining <= 0:
                return self._post_dispatch_failure(
                    adapter=adapter,
                    start=start,
                    descriptor=descriptor,
                    error_kind="transport",
                    error_code=(
                        "late_result_rejected"
                        if wall_remaining <= 0
                        else timeout_code
                    ),
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            validated_result = (
                raw_result
                if isinstance(raw_result, ProviderAttemptResult)
                else ProviderAttemptResult.model_validate(raw_result)
            )
            result = _snapshot_provider_result(validated_result)
        except TimeoutError:
            if physical_request != start.request:
                return self._post_dispatch_failure(
                    adapter=adapter,
                    start=start,
                    descriptor=descriptor,
                    error_kind="protocol",
                    error_code="adapter_request_mutation",
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            return self._post_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code=timeout_code,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception:
            if physical_request != start.request:
                return self._post_dispatch_failure(
                    adapter=adapter,
                    start=start,
                    descriptor=descriptor,
                    error_kind="protocol",
                    error_code="adapter_request_mutation",
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            return self._post_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="internal",
                error_code="unexpected_adapter_exception",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        try:
            completed_source_descriptor = self._snapshot_descriptor(
                source.descriptor
            )
            completed_descriptor = self._snapshot_descriptor(adapter.descriptor)
        except Exception:
            completed_source_descriptor = None
            completed_descriptor = None
        if any(
            candidate is None
            or not self._opened_descriptor_matches_start(
                plan=plan,
                index=index,
                source=source,
                start=start,
                descriptor=descriptor,
                current=candidate,
            )
            for candidate in (
                completed_source_descriptor,
                completed_descriptor,
            )
        ):
            return self._post_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="descriptor_authorization_changed",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        if physical_request != start.request:
            return self._post_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="adapter_request_mutation",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        wall_remaining = self._remaining(plan)
        deadline_remaining = deadline_monotonic - time.monotonic()
        if wall_remaining <= 0 or deadline_remaining <= 0:
            return self._post_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code=(
                    "late_result_rejected"
                    if wall_remaining <= 0
                    else timeout_code
                ),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        if not _attempt_start_matches_plan(
            start, plan, index
        ) or not self._result_matches_start(result, start, descriptor):
            return self._post_dispatch_failure(
                adapter=adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="adapter_contract_mismatch",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        if result.status == "failed" and self._sampling_effect_requires_indeterminate(
            channel=start.channel,
            adapter=adapter,
        ):
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="internal",
                error_code="sampling_effect_indeterminate",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        return result

    def _source_descriptor_matches_start(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        source: ProviderAttemptAdapterSource,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
    ) -> bool:
        try:
            current = self._snapshot_descriptor(source.descriptor)
        except Exception:
            return False
        return all(
            (
                self._descriptor_matches_start(
                    plan=plan,
                    index=index,
                    start=start,
                    descriptor=current,
                ),
                isinstance(source, _StaticProviderAttemptAdapterSource)
                or current == descriptor,
            )
        )

    def _opened_descriptor_matches_start(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        source: ProviderAttemptAdapterSource,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
        current: ProviderDescriptor,
    ) -> bool:
        return all(
            (
                self._descriptor_matches_start(
                    plan=plan,
                    index=index,
                    start=start,
                    descriptor=current,
                ),
                isinstance(source, _StaticProviderAttemptAdapterSource)
                or current == descriptor,
            )
        )

    @staticmethod
    async def _run_context_exit(
        manager: AbstractAsyncContextManager[ProviderAdapter],
        failure: BaseException | None,
    ) -> _AdapterCleanupOutcome:
        try:
            if failure is None:
                await manager.__aexit__(None, None, None)
            else:
                await manager.__aexit__(
                    type(failure),
                    failure,
                    failure.__traceback__,
                )
        except BaseException:
            return _AdapterCleanupOutcome(status="failed")
        return _AdapterCleanupOutcome(status="complete")

    @staticmethod
    def _consume_background_task(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    async def _close_attempt_context(
        self,
        manager: AbstractAsyncContextManager[ProviderAdapter],
        failure: BaseException | None,
    ) -> _AdapterCleanupOutcome:
        """Bound context cleanup and shield it from raw caller cancellation."""

        cleanup_deadline = (
            time.monotonic() + self._adapter_cleanup_timeout_seconds
        )
        exit_task = asyncio.create_task(self._run_context_exit(manager, failure))

        async def bounded_cleanup() -> _AdapterCleanupOutcome:
            done, _ = await asyncio.wait(
                {exit_task},
                timeout=max(0.0, cleanup_deadline - time.monotonic()),
            )
            if exit_task not in done:
                exit_task.cancel()
                exit_task.add_done_callback(self._consume_background_task)
                return _AdapterCleanupOutcome(status="timed_out")
            outcome = exit_task.result()
            if time.monotonic() >= cleanup_deadline:
                return _AdapterCleanupOutcome(status="timed_out")
            return outcome

        cleanup_task = asyncio.create_task(bounded_cleanup())
        while True:
            try:
                return await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # The exact same cleanup task remains authoritative. Repeated
                # raw Task.cancel() calls may delay this waiter, but cannot
                # bypass cleanup and advance terminal persistence.
                continue

    def _source_failure(
        self,
        *,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
        error_code: str,
    ) -> ProviderAttemptResult:
        return self._normalized_failure(
            start=start,
            descriptor=descriptor,
            error_kind="internal",
            error_code=error_code,
            duration_ms=0,
        )

    def _source_descriptor_failure(
        self,
        *,
        source: ProviderAttemptAdapterSource,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
    ) -> ProviderAttemptResult:
        if isinstance(source, _StaticProviderAttemptAdapterSource):
            return self._pre_dispatch_failure(
                adapter=source.adapter,
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="descriptor_authorization_changed",
                duration_ms=0,
            )
        return self._source_failure(
            start=start,
            descriptor=descriptor,
            error_code="adapter_source_descriptor_changed",
        )

    async def _project_terminal_result(
        self,
        result: ProviderAttemptResult,
        *,
        attempt_id: str,
        deadline_monotonic: float,
    ) -> tuple[_PreparedTerminalResult | None, str | None, bool]:
        work, error = self._start_terminal_projection(
            result,
            attempt_id=attempt_id,
        )
        if work is None:
            return None, error, False
        return await self._finish_terminal_projection(
            work,
            result=result,
            attempt_id=attempt_id,
            deadline_monotonic=deadline_monotonic,
        )

    async def _projection_worker(
        self,
        attempt_id: str,
        result: ProviderAttemptResult,
        redaction_snapshot: ProviderRedactionSnapshot,
    ) -> tuple[float, Any]:
        projected = await asyncio.to_thread(
            self._store.canonical_provider_attempt_result,
            attempt_id,
            result,
            redaction_snapshot,
        )
        return time.monotonic(), projected

    def _start_terminal_projection(
        self,
        result: ProviderAttemptResult,
        *,
        attempt_id: str,
    ) -> tuple[_TerminalProjectionWork | None, str | None]:
        """Snapshot a result and start projection without delaying source cleanup."""

        try:
            redaction_snapshot = capture_provider_redaction_snapshot()
            authoritative = _snapshot_provider_result(result)
            authoritative_digest = _provider_result_digest(authoritative)
            projection_input = _snapshot_provider_result(authoritative)
            task = asyncio.create_task(
                self._projection_worker(
                    attempt_id,
                    projection_input,
                    redaction_snapshot,
                )
            )
        except Exception:
            return None, "terminal_projection_failed"
        return (
            _TerminalProjectionWork(
                attempt_id=attempt_id,
                authoritative_digest=authoritative_digest,
                task=task,
            ),
            None,
        )

    async def _abandon_terminal_projection(
        self,
        work: _TerminalProjectionWork | None,
    ) -> None:
        if work is None:
            return
        with contextlib.suppress(Exception):
            await self._store.revoke_provider_attempt_projection(work.attempt_id)
        if not work.task.done():
            work.task.cancel()
            return
        if not work.task.cancelled():
            with contextlib.suppress(BaseException):
                work.task.exception()

    async def _finish_terminal_projection(
        self,
        work: _TerminalProjectionWork,
        *,
        result: ProviderAttemptResult,
        attempt_id: str,
        deadline_monotonic: float,
    ) -> tuple[_PreparedTerminalResult | None, str | None, bool]:
        """Accept only a timely projection of the exact detached result."""

        caller_cancelled = False
        try:
            authoritative = _snapshot_provider_result(result)
            authoritative_digest = _provider_result_digest(authoritative)
            if authoritative_digest != work.authoritative_digest:
                await self._abandon_terminal_projection(work)
                return None, "terminal_projection_mutated_authority", False
            while not work.task.done():
                remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    await self._abandon_terminal_projection(work)
                    return None, "terminal_projection_timed_out", caller_cancelled
                try:
                    await asyncio.wait_for(
                        asyncio.shield(work.task),
                        timeout=remaining,
                    )
                except asyncio.CancelledError:
                    caller_cancelled = True
                    continue
                except TimeoutError:
                    await self._abandon_terminal_projection(work)
                    return None, "terminal_projection_timed_out", caller_cancelled
            completed_at, projected_raw = work.task.result()
            if completed_at > deadline_monotonic:
                await self._abandon_terminal_projection(work)
                return None, "terminal_projection_timed_out", caller_cancelled
            validated_projection = (
                ProviderAttemptCanonicalProjection.model_validate_json(
                    projected_raw.model_dump_json(warnings="error")
                )
                if isinstance(
                    projected_raw,
                    ProviderAttemptCanonicalProjection,
                )
                else ProviderAttemptCanonicalProjection.model_validate(projected_raw)
            )
            projection = ProviderAttemptCanonicalProjection.model_validate_json(
                validated_projection.model_dump_json(warnings="error")
            )
            if projection.attempt_id != attempt_id:
                return None, "terminal_projection_changed_authority", caller_cancelled
            if not self._canonical_projection_matches_result(
                authoritative,
                projection.result,
            ):
                return None, "terminal_projection_changed_authority", caller_cancelled
        except Exception:
            await self._abandon_terminal_projection(work)
            return None, "terminal_projection_failed", caller_cancelled
        if _provider_result_digest(authoritative) != authoritative_digest:
            return None, "terminal_projection_mutated_authority", caller_cancelled
        return (
            _PreparedTerminalResult(
                authoritative_digest=authoritative_digest,
                projection=projection,
            ),
            None,
            caller_cancelled,
        )

    async def _run_adapter_lifecycle(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        source: ProviderAttemptAdapterSource,
        descriptor: ProviderDescriptor,
        start: ProviderAttemptStart,
        deadline_monotonic: float,
        timeout_code: Literal["ttl_expired", "timeout"],
    ) -> _AdapterLifecycleOutcome:
        """Own source entry, one effect, and close as a single task."""

        source_start = ProviderAttemptStart.model_validate_json(
            start.model_dump_json(warnings="error")
        )
        manager: AbstractAsyncContextManager[ProviderAdapter] | None = None
        adapter: ProviderAdapter | None = None
        entry_attempted = False
        entered = False
        cancellation: asyncio.CancelledError | None = None
        lifecycle_failure: BaseException | None = None
        result: ProviderAttemptResult | None = None
        prepared: _PreparedTerminalResult | None = None
        projection_work: _TerminalProjectionWork | None = None
        indeterminate_reason: str | None = None
        try:
            if source_start != start:
                result = self._source_failure(
                    start=start,
                    descriptor=descriptor,
                    error_code="adapter_source_start_mutation",
                )
            elif not self._source_descriptor_matches_start(
                plan=plan,
                index=index,
                source=source,
                start=start,
                descriptor=descriptor,
            ):
                result = self._source_descriptor_failure(
                    source=source,
                    start=start,
                    descriptor=descriptor,
                )
            else:
                remaining = self._remaining(plan)
                deadline_remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0 or deadline_remaining <= 0:
                    result = self._normalized_failure(
                        start=start,
                        descriptor=descriptor,
                        error_kind="transport",
                        error_code=(
                            "ttl_expired" if remaining <= 0 else timeout_code
                        ),
                        duration_ms=0,
                    )

            if result is None:
                manager = source.open_attempt(source_start)
                if source_start != start:
                    result = self._source_failure(
                        start=start,
                        descriptor=descriptor,
                        error_code="adapter_source_start_mutation",
                    )
                elif not self._source_descriptor_matches_start(
                    plan=plan,
                    index=index,
                    source=source,
                    start=start,
                    descriptor=descriptor,
                ):
                    result = self._source_descriptor_failure(
                        source=source,
                        start=start,
                        descriptor=descriptor,
                    )
                else:
                    remaining = self._remaining(plan)
                    entry_timeout = min(
                        remaining,
                        deadline_monotonic - time.monotonic(),
                    )
                    if entry_timeout <= 0:
                        result = self._normalized_failure(
                            start=start,
                            descriptor=descriptor,
                            error_kind="transport",
                            error_code=(
                                "ttl_expired" if remaining <= 0 else timeout_code
                            ),
                            duration_ms=0,
                        )

            if result is None and manager is not None:
                entry_attempted = True
                try:
                    async with asyncio.timeout(entry_timeout):
                        adapter = await manager.__aenter__()
                    entered = True
                except TimeoutError as exc:
                    lifecycle_failure = exc
                    result = self._normalized_failure(
                        start=start,
                        descriptor=descriptor,
                        error_kind="transport",
                        error_code=timeout_code,
                        duration_ms=0,
                    )

            if result is None and entered and adapter is not None:
                if source_start != start:
                    result = self._source_failure(
                        start=start,
                        descriptor=descriptor,
                        error_code="adapter_source_start_mutation",
                    )
                elif not self._source_descriptor_matches_start(
                    plan=plan,
                    index=index,
                    source=source,
                    start=start,
                    descriptor=descriptor,
                ):
                    result = self._source_descriptor_failure(
                        source=source,
                        start=start,
                        descriptor=descriptor,
                    )
                else:
                    try:
                        adapter_descriptor = self._snapshot_descriptor(
                            adapter.descriptor
                        )
                    except Exception:
                        adapter_descriptor = None
                    if (
                        adapter_descriptor is None
                        or not self._opened_descriptor_matches_start(
                            plan=plan,
                            index=index,
                            source=source,
                            start=start,
                            descriptor=descriptor,
                            current=adapter_descriptor,
                        )
                    ):
                        result = self._pre_dispatch_failure(
                            adapter=adapter,
                            start=start,
                            descriptor=descriptor,
                            error_kind="protocol",
                            error_code="descriptor_authorization_changed",
                            duration_ms=0,
                        )
                    else:
                        remaining = self._remaining(plan)
                        deadline_remaining = deadline_monotonic - time.monotonic()
                        if remaining <= 0 or deadline_remaining <= 0:
                            result = self._pre_dispatch_failure(
                                adapter=adapter,
                                start=start,
                                descriptor=descriptor,
                                error_kind="transport",
                                error_code=(
                                    "ttl_expired"
                                    if remaining <= 0
                                    else timeout_code
                                ),
                                duration_ms=0,
                            )

            if result is None and adapter is not None:
                result = await self._invoke_adapter(
                    plan=plan,
                    index=index,
                    source=source,
                    adapter=adapter,
                    descriptor=descriptor,
                    start=start,
                    deadline_monotonic=deadline_monotonic,
                    timeout_code=timeout_code,
                )
            # Returned content must be snapshotted before source cleanup can
            # rotate credentials. Failures carry no provider content, so wait
            # until cleanup and all final authority checks have settled before
            # projecting them. This avoids timing-dependent failure digests.
            if result is not None and result.response is not None:
                projection_work, projection_error = self._start_terminal_projection(
                    result,
                    attempt_id=start.attempt_id,
                )
                if projection_work is None:
                    result = None
                    indeterminate_reason = (
                        projection_error or "terminal_projection_failed"
                    )
        except asyncio.CancelledError as exc:
            cancellation = exc
            lifecycle_failure = exc
            if adapter is None:
                result = self._source_failure(
                    start=start,
                    descriptor=descriptor,
                    error_code="adapter_source_cancelled",
                )
            else:
                result = self._post_dispatch_failure(
                    adapter=adapter,
                    start=start,
                    descriptor=descriptor,
                    error_kind="transport",
                    error_code="broker_cancelled",
                    duration_ms=0,
                )
        except Exception as exc:
            lifecycle_failure = exc
            result = self._source_failure(
                start=start,
                descriptor=descriptor,
                error_code=(
                    "adapter_source_lifecycle_failed"
                    if entered
                    else "adapter_source_open_failed"
                ),
            )
        finally:
            if entry_attempted and manager is not None:
                cleanup = await self._close_attempt_context(
                    manager,
                    lifecycle_failure,
                )
                if cleanup.status == "timed_out":
                    result = None
                    indeterminate_reason = "adapter_source_cleanup_timed_out"
                elif cleanup.status == "failed":
                    result = None
                    indeterminate_reason = "adapter_source_cleanup_failed"

        if result is not None and source_start != start:
            result = self._source_failure(
                start=start,
                descriptor=descriptor,
                error_code="adapter_source_start_mutation",
            )
        if result is not None and not self._source_descriptor_matches_start(
            plan=plan,
            index=index,
            source=source,
            start=start,
            descriptor=descriptor,
        ):
            result = self._source_descriptor_failure(
                source=source,
                start=start,
                descriptor=descriptor,
            )
        if result is None:
            await self._abandon_terminal_projection(projection_work)
            prepared = None
        else:
            result_digest = _provider_result_digest(result)
            if (
                projection_work is None
                or projection_work.authoritative_digest != result_digest
            ):
                authority_changed = projection_work is not None
                await self._abandon_terminal_projection(projection_work)
                projection_work = None
                if authority_changed:
                    result = None
                    indeterminate_reason = (
                        "terminal_authority_changed_after_projection"
                    )
                elif result.response is not None:
                    result = None
                    indeterminate_reason = "terminal_projection_not_prepared"
                else:
                    projection_work, projection_error = (
                        self._start_terminal_projection(
                            result,
                            attempt_id=start.attempt_id,
                        )
                    )
                    if projection_work is None:
                        result = None
                        indeterminate_reason = (
                            projection_error or "terminal_projection_failed"
                        )
            if result is not None and projection_work is not None:
                projection_deadline = (
                    deadline_monotonic
                    if result.response is not None
                    else max(
                        deadline_monotonic,
                        time.monotonic() + self._terminal_write_timeout_seconds,
                    )
                )
                prepared, projection_error, projection_cancelled = (
                    await self._finish_terminal_projection(
                        projection_work,
                        result=result,
                        attempt_id=start.attempt_id,
                        deadline_monotonic=projection_deadline,
                    )
                )
                if projection_cancelled and cancellation is None:
                    cancellation = asyncio.CancelledError()
                if prepared is None:
                    result = None
                    indeterminate_reason = (
                        projection_error or "terminal_projection_failed"
                    )
        return _AdapterLifecycleOutcome(
            result=result,
            prepared=prepared,
            cancelled=cancellation is not None,
            indeterminate_reason=indeterminate_reason,
        )

    async def _open_attempt_adapter(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        source: ProviderAttemptAdapterSource,
        descriptor: ProviderDescriptor,
        start: ProviderAttemptStart,
        deadline_monotonic: float,
        timeout_code: Literal["ttl_expired", "timeout"],
    ) -> _AdapterLifecycleOutcome:
        lifecycle = asyncio.create_task(
            self._run_adapter_lifecycle(
                plan=plan,
                index=index,
                source=source,
                descriptor=descriptor,
                start=start,
                deadline_monotonic=deadline_monotonic,
                timeout_code=timeout_code,
            )
        )
        caller_cancelled = False
        while True:
            try:
                outcome = await asyncio.shield(lifecycle)
                break
            except asyncio.CancelledError:
                caller_cancelled = True
                if not lifecycle.done():
                    lifecycle.cancel()
                    continue
                if lifecycle.cancelled():
                    raise BrokerCancellationPersistenceError(
                        "adapter lifecycle cancellation bypassed cleanup"
                    ) from None
                outcome = lifecycle.result()
                break
        if caller_cancelled and not outcome.cancelled:
            outcome = _AdapterLifecycleOutcome(
                result=outcome.result,
                prepared=outcome.prepared,
                cancelled=True,
                indeterminate_reason=outcome.indeterminate_reason,
            )
        return outcome

    async def _stored_result(
        self,
        start: ProviderAttemptStart,
    ) -> tuple[ProviderAttemptResult | None, str | None]:
        rows = await self._store.list_provider_attempts(
            supervisor_session_id=start.request.supervision.session_id,
            delegation_id=start.delegation_id,
            limit=100,
        )
        row = next(
            (item for item in rows if item.get("attempt_id") == start.attempt_id),
            None,
        )
        if row is None:
            return None, "replay_row_missing"
        try:
            stored_start = self._reconstruct_stored_start(row)
        except Exception:
            return None, "replay_start_decode_failed"
        if stored_start != start:
            return None, "replay_start_mismatch"
        return self._decode_stored_result(row, start)

    @staticmethod
    def _reconstruct_stored_start(
        row: Mapping[str, Any],
    ) -> ProviderAttemptStart:
        """Rebuild the typed start from the ledger's canonical frozen prompt."""

        start_raw = row.get("start_json")
        prompt_raw = row.get("prompt_text")
        if not isinstance(start_raw, str) or not isinstance(prompt_raw, str):
            raise ValueError("stored attempt lacks canonical start evidence")
        if _content_digest(start_raw) != row.get("start_digest"):
            raise ValueError("stored attempt start digest mismatch")
        if _content_digest(prompt_raw) != row.get("prompt_digest"):
            raise ValueError("stored attempt prompt digest mismatch")
        try:
            start_record = json.loads(start_raw)
            prompt_record = json.loads(prompt_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("stored attempt evidence is malformed") from exc
        if (
            json.dumps(
                start_record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            != start_raw
            or json.dumps(
                prompt_record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            != prompt_raw
        ):
            raise ValueError("stored attempt evidence is not canonical")
        if not isinstance(prompt_record, list) or len(prompt_record) < 2:
            raise ValueError("stored attempt prompt lacks its TTL envelope")

        visible = tuple(ProviderMessage.model_validate(item) for item in prompt_record)
        request_record = start_record.get("request")
        if not isinstance(request_record, dict):
            raise ValueError("stored attempt request is malformed")
        if start_record.get("model_visible_prompt_digest") != row.get("prompt_digest"):
            raise ValueError("stored attempt prompt is not bound to its start")
        request_record["messages"] = [
            message.model_dump(mode="json") for message in visible[1:]
        ]
        for metadata_key in (
            "model_visible_prompt_digest",
            "prompt_redaction",
            "started_at",
        ):
            start_record.pop(metadata_key, None)
        start = ProviderAttemptStart.model_validate_json(
            json.dumps(
                start_record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        if model_visible_messages(start.request) != visible:
            raise ValueError("stored attempt TTL or model-visible prompt changed")
        supervision = start.request.supervision
        projections = {
            "attempt_id": start.attempt_id,
            "delegation_id": start.delegation_id,
            "attempt_ordinal": start.attempt_ordinal,
            "supervisor_plane": start.supervisor_plane,
            "supervisor_model": start.supervisor_model,
            "provider": start.provider.value,
            "channel": start.channel.value,
            "credential_plane": start.credential_plane.value,
            "requested_model": start.requested_model,
            "request_id": start.request.request_id,
            "route": start.request.route.value,
            "supervisor_session_id": supervision.session_id,
            "objective_id": supervision.objective_id,
            "route_decision_id": supervision.route_decision_id,
        }
        if any(
            str(row.get(field)) != str(expected)
            for field, expected in projections.items()
        ):
            raise ValueError("stored attempt start projection changed")
        try:
            row_ttl = datetime.fromisoformat(str(row.get("ttl_expires_at")))
        except (TypeError, ValueError) as exc:
            raise ValueError("stored attempt TTL projection is malformed") from exc
        if row_ttl != supervision.ttl_expires_at:
            raise ValueError("stored attempt TTL projection changed")
        return start

    @staticmethod
    def _stored_start_matches_plan(
        start: ProviderAttemptStart,
        plan: GrokDelegationPlan,
        index: int,
    ) -> bool:
        return _attempt_start_matches_plan(start, plan, index)

    def _decode_stored_result(
        self,
        row: Mapping[str, Any],
        start: ProviderAttemptStart,
    ) -> tuple[ProviderAttemptResult | None, str | None]:
        status = str(row.get("transport_status") or "")
        if status in {"started", "indeterminate"}:
            return None, "replay_not_returnable"
        try:
            if status == "returned":
                receipt = ProviderReceipt.model_validate_json(
                    json.dumps(row.get("receipt"), separators=(",", ":"))
                )
                response = ProviderResponse(
                    provider=receipt.provider,
                    channel=receipt.channel,
                    model=str(row.get("resolved_model") or ""),
                    text=str(row.get("output_text") or ""),
                    finish_reason=row.get("finish_reason"),
                    receipt=receipt,
                )
                result = ProviderAttemptResult(status="returned", response=response)
            elif status == "failed":
                failure = ProviderFailureReceipt.model_validate_json(
                    json.dumps(row.get("receipt"), separators=(",", ":"))
                )
                result = ProviderAttemptResult(status="failed", failure=failure)
            else:
                return None, "replay_status_invalid"
        except Exception:
            return None, "replay_decode_failed"
        if not provider_result_matches_start(start, result):
            return None, "replay_contract_mismatch"
        if row.get("canonical_result_digest") != _provider_result_digest(result):
            return None, "replay_result_digest_mismatch"
        return result, None

    def _stored_conflict_result(
        self,
        plan: GrokDelegationPlan,
        index: int,
        reason: str,
        attempts: tuple[BrokerAttemptEvidence, ...] = (),
    ) -> BrokerDelegationResult:
        delegation = plan.delegations[index]
        return BrokerDelegationResult(
            delegation_id=_delegation_id(plan, index),
            delegation_key=delegation.delegation_key,
            provider=delegation.provider,
            route=delegation.route,
            status="indeterminate",
            reason=reason,
            attempts=attempts,
        )

    async def _replay_delegation(
        self,
        plan: GrokDelegationPlan,
        index: int,
    ) -> BrokerDelegationResult | None:
        """Replay any existing exact delegation before consulting availability.

        Availability and mutable credential state are deliberately excluded
        from attempt identity. Once any physical effect has been recorded for
        an exact delegation, a later execution may only replay that sequence
        or fail indeterminate; it may never choose a newly available lane.
        """

        delegation = plan.delegations[index]
        delegation_id = _delegation_id(plan, index)
        try:
            rows = await self._store.list_provider_attempts(
                supervisor_session_id=plan.supervision.session_id,
                delegation_id=delegation_id,
                limit=100,
            )
        except Exception:
            return self._stored_conflict_result(
                plan,
                index,
                "stored_attempt_read_failed",
            )
        if not rows:
            return None
        if len(rows) > MAX_PHYSICAL_ATTEMPTS_PER_DELEGATION:
            return self._stored_conflict_result(
                plan,
                index,
                "stored_attempt_conflict",
            )

        try:
            ordered = sorted(rows, key=lambda row: int(row.get("attempt_ordinal")))
            ordinals = [int(row.get("attempt_ordinal")) for row in ordered]
            if ordinals not in ([1], [2], [1, 2]):
                raise ValueError("stored attempt ordinal conflict")

            evidence: list[BrokerAttemptEvidence] = []
            has_indeterminate = False
            for row in ordered:
                start = self._reconstruct_stored_start(row)
                if not self._stored_start_matches_plan(start, plan, index):
                    raise ValueError("stored start does not match the exact plan")
                stored_result, error = self._decode_stored_result(
                    row,
                    start,
                )
                if stored_result is None:
                    if str(row.get("transport_status") or "") not in {
                        "started",
                        "indeterminate",
                    }:
                        raise ValueError(error or "stored terminal result is invalid")
                    has_indeterminate = True
                    evidence.append(
                        BrokerAttemptEvidence(
                            start=start,
                            persistence="replay_indeterminate",
                            harvest=BrokerHarvestStatus(
                                status="not_applicable",
                                reason=(
                                    "stored_attempt_started"
                                    if row.get("transport_status") == "started"
                                    else "stored_attempt_indeterminate"
                                ),
                            ),
                        )
                    )
                else:
                    evidence.append(
                        BrokerAttemptEvidence(
                            start=start,
                            persistence="replayed_terminal",
                            result=stored_result,
                            harvest=BrokerHarvestStatus(
                                status="not_applicable",
                                reason="delegation_replayed",
                            ),
                        )
                    )

            if len(evidence) == 2:
                first_result = evidence[0].result
                if first_result is None or first_result.status != "failed":
                    raise ValueError("fallback sequence lacks a durable first failure")
            if has_indeterminate:
                return self._stored_conflict_result(
                    plan,
                    index,
                    "stored_attempt_indeterminate",
                    tuple(evidence),
                )

            final = evidence[-1]
            if final.result is None:
                raise ValueError("stored terminal sequence has no result")
            if final.result.status == "returned":
                return BrokerDelegationResult(
                    delegation_id=delegation_id,
                    delegation_key=delegation.delegation_key,
                    provider=delegation.provider,
                    route=delegation.route,
                    status="returned",
                    reason=(
                        "subscription_returned"
                        if final.start.attempt_ordinal == 1
                        else "same_provider_api_returned"
                    ),
                    attempts=tuple(evidence),
                )
            failure = final.result.failure
            return BrokerDelegationResult(
                delegation_id=delegation_id,
                delegation_key=delegation.delegation_key,
                provider=delegation.provider,
                route=delegation.route,
                status="failed",
                reason=(
                    "same_provider_api_failed"
                    if final.start.attempt_ordinal == 2
                    else (
                        "subscription_failed_no_fallback"
                        if failure is not None and failure.error_kind == "internal"
                        else "same_provider_api_unavailable"
                    )
                ),
                attempts=tuple(evidence),
            )
        except Exception:
            return self._stored_conflict_result(
                plan,
                index,
                "stored_attempt_conflict",
            )

    async def _harvest(
        self,
        plan: GrokDelegationPlan,
    ) -> BrokerHarvestStatus:
        if self._harvester is None:
            return BrokerHarvestStatus(
                status="unavailable",
                reason="trigger_not_configured",
            )
        remaining = self._remaining(plan)
        if remaining <= 0:
            return BrokerHarvestStatus(status="ttl_expired", reason="ttl_expired")
        timeout = min(remaining, self._harvest_timeout_seconds)
        deadline_monotonic = time.monotonic() + timeout
        try:
            # Lock acquisition and the trigger share one deadline.  A queued
            # trigger cannot wait outside the supervisor lease and then start
            # a late cloud effect.
            async with asyncio.timeout(timeout):
                async with self._harvest_lock:
                    if self._remaining(plan) <= 0:
                        return BrokerHarvestStatus(
                            status="ttl_expired",
                            reason="ttl_expired",
                        )
                    raw = await self._harvester.run_once(
                        self._store,
                        deadline_monotonic=deadline_monotonic,
                    )
            status = str(getattr(raw, "status", ""))
            if status not in {"complete", "partial", "idle", "unavailable"}:
                raise ValueError("invalid harvest status")
            reason_raw = getattr(raw, "reason", None)
            reason = str(reason_raw) if reason_raw is not None else None
            return BrokerHarvestStatus(
                status=status,
                reason=reason,
                leased=int(getattr(raw, "leased", 0)),
                synced=int(getattr(raw, "synced", 0)),
                retry_wait=int(getattr(raw, "retry_wait", 0)),
                lease_lost=int(getattr(raw, "lease_lost", 0)),
                state_errors=int(getattr(raw, "state_errors", 0)),
            )
        except TimeoutError:
            if self._remaining(plan) <= 0:
                return BrokerHarvestStatus(
                    status="ttl_expired",
                    reason="ttl_expired",
                )
            return BrokerHarvestStatus(status="timed_out", reason="trigger_timed_out")
        except Exception:
            return BrokerHarvestStatus(status="failed", reason="trigger_failed")

    async def _bounded_terminal_stage(
        self,
        task: asyncio.Task[Any],
        *,
        deadline_monotonic: float,
        label: str,
    ) -> tuple[Any | None, BaseException | None]:
        done, _ = await asyncio.wait(
            {task},
            timeout=max(0.0, deadline_monotonic - time.monotonic()),
        )
        if task not in done:
            task.cancel()
            task.add_done_callback(self._consume_background_task)
            return None, TimeoutError(f"{label} timed out")
        try:
            value = task.result()
            error: BaseException | None = None
        except BaseException as exc:
            value = None
            error = exc
        if time.monotonic() >= deadline_monotonic:
            return None, TimeoutError(f"{label} timed out")
        return value, error

    @staticmethod
    def _canonical_projection_matches_result(
        original: ProviderAttemptResult,
        projected: ProviderAttemptResult,
    ) -> bool:
        """Allow the store to transform only returned response text."""

        if original.status != projected.status:
            return False
        if original.status == "failed":
            return original == projected
        if original.response is None or projected.response is None:
            return False
        restored = projected.model_copy(
            update={
                "response": projected.response.model_copy(
                    update={"text": original.response.text}
                )
            }
        )
        return restored == original

    async def _run_terminal_persistence(
        self,
        *,
        start: ProviderAttemptStart,
        result: ProviderAttemptResult,
        prepared: _PreparedTerminalResult | None,
    ) -> _TerminalPersistenceOutcome:
        """Run one terminal attempt and always revoke its ephemeral authority."""

        try:
            return await self._run_terminal_persistence_once(
                start=start,
                result=result,
                prepared=prepared,
            )
        finally:
            await self._store.revoke_provider_attempt_projection(
                start.attempt_id
            )

    async def _run_terminal_persistence_once(
        self,
        *,
        start: ProviderAttemptStart,
        result: ProviderAttemptResult,
        prepared: _PreparedTerminalResult | None,
    ) -> _TerminalPersistenceOutcome:
        """Own one bounded terminal write and its exact durable replay check."""

        deadline = time.monotonic() + self._terminal_write_timeout_seconds
        try:
            authoritative = _snapshot_provider_result(result)
            authoritative_digest = _provider_result_digest(authoritative)
            if prepared is None:
                raise ValueError("terminal result lacks a canonical projection")
            if prepared.authoritative_digest != authoritative_digest:
                raise ValueError("prepared terminal authority changed")
            projection = ProviderAttemptCanonicalProjection.model_validate_json(
                prepared.projection.model_dump_json(warnings="error")
            )
            if projection.attempt_id != start.attempt_id:
                raise ValueError("prepared projection is bound to another attempt")
            projected = _snapshot_provider_result(projection.result)
            projected_digest = _provider_result_digest(projected)
            if projected_digest != projection.result_digest:
                raise ValueError("prepared terminal projection changed")
        except Exception as exc:
            return _TerminalPersistenceOutcome(result=None, error=exc)
        if not self._canonical_projection_matches_result(
            authoritative,
            projected,
        ):
            return _TerminalPersistenceOutcome(
                result=None,
                error=ValueError("terminal canonical projection changed authority"),
            )
        if not provider_result_matches_start(start, projected):
            return _TerminalPersistenceOutcome(
                result=None,
                error=ValueError("terminal canonical projection changed contract"),
            )

        write_input = ProviderAttemptCanonicalProjection.model_validate_json(
            projection.model_dump_json(warnings="error")
        )
        write = asyncio.create_task(
            self._store.complete_projected_provider_attempt(
                start.attempt_id,
                write_input,
            )
        )
        stored, write_error = await self._bounded_terminal_stage(
            write,
            deadline_monotonic=deadline,
            label="terminal write",
        )
        if write_error is not None:
            return _TerminalPersistenceOutcome(result=None, error=write_error)
        if type(stored) is not bool:
            return _TerminalPersistenceOutcome(
                result=None,
                error=TypeError("terminal store returned a non-boolean result"),
            )

        replay_read = asyncio.create_task(self._stored_result(start))
        replay_outcome, replay_task_error = await self._bounded_terminal_stage(
            replay_read,
            deadline_monotonic=deadline,
            label="terminal replay check",
        )
        if replay_task_error is not None:
            return _TerminalPersistenceOutcome(
                result=None,
                stored=stored,
                error=replay_task_error,
            )
        replay, replay_error = replay_outcome
        if (
            replay is None
            or _provider_result_digest(replay) != projected_digest
            or replay != projected
        ):
            return _TerminalPersistenceOutcome(
                result=None,
                stored=stored,
                error=ValueError(
                    replay_error or "terminal replay changed canonical result"
                ),
            )
        return _TerminalPersistenceOutcome(result=replay, stored=stored)

    async def _write_terminal(
        self,
        *,
        start: ProviderAttemptStart,
        result: ProviderAttemptResult,
        prepared: _PreparedTerminalResult | None,
    ) -> ProviderAttemptResult:
        """Shield one authoritative persistence task from repeated cancellation.

        Cancellation of the broker task never cancels a write that already
        received the provider result.  The original cancellation propagates
        only after exact durable replay is known.
        """

        persistence = asyncio.create_task(
            self._run_terminal_persistence(
                start=start,
                result=result,
                prepared=prepared,
            )
        )
        caller_cancelled = False
        while True:
            try:
                outcome = await asyncio.shield(persistence)
                break
            except asyncio.CancelledError:
                if persistence.cancelled():
                    raise BrokerCancellationPersistenceError(
                        "authoritative terminal persistence task was cancelled"
                    ) from None
                caller_cancelled = True
                continue

        if outcome.result is None or outcome.error is not None:
            if caller_cancelled:
                raise BrokerCancellationPersistenceError(
                    "cancelled provider attempt could not be terminalized"
                ) from outcome.error
            raise RuntimeError("provider attempt terminal persistence failed") from (
                outcome.error
            )
        if caller_cancelled:
            raise asyncio.CancelledError
        return outcome.result

    async def _attempt(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        channel: ProviderChannel,
        source: ProviderAttemptAdapterSource,
        descriptor: ProviderDescriptor,
        ordinal: Literal[1, 2],
    ) -> BrokerAttemptEvidence:
        delegation = plan.delegations[index]
        current_descriptor = self._snapshot_descriptor(source.descriptor)
        if (
            not self._descriptor_is_authorized(
                delegation=delegation,
                channel=channel,
                plane=current_descriptor.credential_plane,
                descriptor=current_descriptor,
            )
            or current_descriptor.credential_state == CredentialState.MISSING
            or delegation.route not in current_descriptor.supported_routes
        ):
            raise ValueError("descriptor authorization changed before durable begin")
        descriptor = current_descriptor
        start = self._start(
            plan=plan,
            index=index,
            channel=channel,
            descriptor=descriptor,
            ordinal=ordinal,
        )
        try:
            is_new = await self._store.begin_provider_attempt(start)
        except Exception:
            return BrokerAttemptEvidence(
                start=start,
                persistence="begin_failed",
                harvest=BrokerHarvestStatus(
                    status="not_applicable",
                    reason="begin_failed",
                ),
            )
        if not is_new:
            try:
                stored_result, error = await self._stored_result(start)
            except Exception:
                stored_result, error = None, "replay_read_failed"
            if stored_result is None:
                return BrokerAttemptEvidence(
                    start=start,
                    persistence="replay_indeterminate",
                    harvest=BrokerHarvestStatus(
                        status="not_applicable",
                        reason=error or "replay_indeterminate",
                    ),
                )
            return BrokerAttemptEvidence(
                start=start,
                persistence="replayed_terminal",
                result=stored_result,
                harvest=BrokerHarvestStatus(
                    status="not_applicable",
                    reason="delegation_replayed",
                ),
            )

        remaining = self._remaining(plan)
        if remaining <= 0:
            outcome = _AdapterLifecycleOutcome(
                result=self._normalized_failure(
                    start=start,
                    descriptor=descriptor,
                    error_kind="transport",
                    error_code="ttl_expired",
                    duration_ms=0,
                )
            )
        elif not self._source_descriptor_matches_start(
            plan=plan,
            index=index,
            source=source,
            start=start,
            descriptor=descriptor,
        ):
            outcome = _AdapterLifecycleOutcome(
                result=self._source_descriptor_failure(
                    source=source,
                    start=start,
                    descriptor=descriptor,
                )
            )
        else:
            physical_budget = min(
                remaining,
                start.request.timeout_seconds,
                descriptor.max_timeout_seconds,
            )
            timeout_code: Literal["ttl_expired", "timeout"] = (
                "ttl_expired"
                if remaining
                <= min(
                    start.request.timeout_seconds,
                    descriptor.max_timeout_seconds,
                )
                else "timeout"
            )
            outcome = await self._open_attempt_adapter(
                plan=plan,
                index=index,
                source=source,
                descriptor=descriptor,
                start=start,
                deadline_monotonic=time.monotonic() + physical_budget,
                timeout_code=timeout_code,
            )
        if outcome.result is not None and outcome.prepared is None:
            prepared, projection_error, projection_cancelled = (
                await self._project_terminal_result(
                    outcome.result,
                    attempt_id=start.attempt_id,
                    deadline_monotonic=(
                        time.monotonic() + self._terminal_write_timeout_seconds
                    ),
                )
            )
            outcome = _AdapterLifecycleOutcome(
                result=outcome.result if prepared is not None else None,
                prepared=prepared,
                cancelled=outcome.cancelled or projection_cancelled,
                indeterminate_reason=(
                    outcome.indeterminate_reason
                    if prepared is not None
                    else projection_error or "terminal_projection_failed"
                ),
            )
        if outcome.result is None:
            if outcome.cancelled:
                raise BrokerCancellationPersistenceError(
                    "cancelled adapter source cleanup did not complete"
                )
            return BrokerAttemptEvidence(
                start=start,
                persistence="terminal_indeterminate",
                harvest=BrokerHarvestStatus(
                    status="not_applicable",
                    reason=(
                        outcome.indeterminate_reason
                        or "adapter_source_lifecycle_indeterminate"
                    ),
                ),
            )
        result = outcome.result
        if outcome.cancelled:
            try:
                await self._write_terminal(
                    start=start,
                    result=result,
                    prepared=outcome.prepared,
                )
            except BrokerCancellationPersistenceError:
                raise
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                raise BrokerCancellationPersistenceError(
                    "cancelled provider attempt could not be terminalized"
                ) from exc
            raise asyncio.CancelledError
        try:
            durable_result = await self._write_terminal(
                start=start,
                result=result,
                prepared=outcome.prepared,
            )
        except BrokerCancellationPersistenceError:
            raise
        except Exception:
            return BrokerAttemptEvidence(
                start=start,
                persistence="terminal_indeterminate",
                harvest=BrokerHarvestStatus(
                    status="not_applicable",
                    reason="terminal_persistence_failed",
                ),
            )
        result = durable_result
        return BrokerAttemptEvidence(
            start=start,
            persistence="durable_terminal",
            result=result,
            harvest=await self._harvest(plan),
        )

    async def _run_delegation(
        self,
        plan: GrokDelegationPlan,
        index: int,
        semaphore: asyncio.Semaphore,
    ) -> BrokerDelegationResult:
        delegation = plan.delegations[index]
        delegation_id = _delegation_id(plan, index)
        async with semaphore:
            replay = await self._replay_delegation(plan, index)
            if replay is not None:
                return replay
            if self._remaining(plan) <= 0:
                return BrokerDelegationResult(
                    delegation_id=delegation_id,
                    delegation_key=delegation.delegation_key,
                    provider=delegation.provider,
                    route=delegation.route,
                    status="expired",
                    reason="ttl_expired",
                )
            try:
                subscription = self._select_channel(
                    delegation=delegation,
                    plane=CredentialPlane.SUBSCRIPTION,
                )
                api = (
                    self._select_channel(
                        delegation=delegation,
                        plane=CredentialPlane.METERED_API,
                    )
                    if delegation.fallback.max_metered_api_attempts == 1
                    else None
                )
            except Exception:
                return BrokerDelegationResult(
                    delegation_id=delegation_id,
                    delegation_key=delegation.delegation_key,
                    provider=delegation.provider,
                    route=delegation.route,
                    status="indeterminate",
                    reason="registry_contract_invalid",
                    attempts=(),
                )

            attempts: list[BrokerAttemptEvidence] = []
            if subscription is not None:
                channel, source, descriptor = subscription
                evidence = await self._attempt(
                    plan=plan,
                    index=index,
                    channel=channel,
                    source=source,
                    descriptor=descriptor,
                    ordinal=1,
                )
                attempts.append(evidence)
                if evidence.result is None:
                    return BrokerDelegationResult(
                        delegation_id=delegation_id,
                        delegation_key=delegation.delegation_key,
                        provider=delegation.provider,
                        route=delegation.route,
                        status="indeterminate",
                        reason="attempt_state_indeterminate",
                        attempts=tuple(attempts),
                    )
                if evidence.result.status == "returned":
                    return BrokerDelegationResult(
                        delegation_id=delegation_id,
                        delegation_key=delegation.delegation_key,
                        provider=delegation.provider,
                        route=delegation.route,
                        status="returned",
                        reason="subscription_returned",
                        attempts=tuple(attempts),
                    )
                failure = evidence.result.failure
                if failure is None or failure.error_kind not in {
                    "configuration",
                    "transport",
                    "protocol",
                }:
                    return BrokerDelegationResult(
                        delegation_id=delegation_id,
                        delegation_key=delegation.delegation_key,
                        provider=delegation.provider,
                        route=delegation.route,
                        status="failed",
                        reason="subscription_failed_no_fallback",
                        attempts=tuple(attempts),
                    )

            if api is None:
                status: Literal["failed", "unavailable"] = (
                    "failed" if attempts else "unavailable"
                )
                return BrokerDelegationResult(
                    delegation_id=delegation_id,
                    delegation_key=delegation.delegation_key,
                    provider=delegation.provider,
                    route=delegation.route,
                    status=status,
                    reason=(
                        "same_provider_api_unavailable"
                        if attempts
                        else "subscription_channel_unavailable"
                    ),
                    attempts=tuple(attempts),
                )
            if self._remaining(plan) <= 0:
                return BrokerDelegationResult(
                    delegation_id=delegation_id,
                    delegation_key=delegation.delegation_key,
                    provider=delegation.provider,
                    route=delegation.route,
                    status="expired",
                    reason="ttl_expired_before_api_fallback",
                    attempts=tuple(attempts),
                )
            channel, source, descriptor = api
            evidence = await self._attempt(
                plan=plan,
                index=index,
                channel=channel,
                source=source,
                descriptor=descriptor,
                ordinal=2,
            )
            attempts.append(evidence)
            if evidence.result is None:
                return BrokerDelegationResult(
                    delegation_id=delegation_id,
                    delegation_key=delegation.delegation_key,
                    provider=delegation.provider,
                    route=delegation.route,
                    status="indeterminate",
                    reason="attempt_state_indeterminate",
                    attempts=tuple(attempts),
                )
            return BrokerDelegationResult(
                delegation_id=delegation_id,
                delegation_key=delegation.delegation_key,
                provider=delegation.provider,
                route=delegation.route,
                status=(
                    "returned" if evidence.result.status == "returned" else "failed"
                ),
                reason=(
                    "same_provider_api_returned"
                    if evidence.result.status == "returned"
                    else "same_provider_api_failed"
                ),
                attempts=tuple(attempts),
            )

    async def _guarded_delegation(
        self,
        plan: GrokDelegationPlan,
        index: int,
        semaphore: asyncio.Semaphore,
    ) -> BrokerDelegationResult:
        try:
            return await self._run_delegation(plan, index, semaphore)
        except BrokerCancellationPersistenceError:
            raise
        except Exception:
            delegation = plan.delegations[index]
            return BrokerDelegationResult(
                delegation_id=_delegation_id(plan, index),
                delegation_key=delegation.delegation_key,
                provider=delegation.provider,
                route=delegation.route,
                status="indeterminate",
                reason="broker_internal_failure",
                attempts=(),
            )

    async def execute(
        self,
        plan: GrokDelegationPlan | Mapping[str, Any],
    ) -> GrokWorkerBrokerResult:
        """Run one plan and return transport evidence for Grok synthesis only."""

        validated = _snapshot_plan(plan)
        self._now()
        semaphore = asyncio.Semaphore(validated.max_concurrency)
        tasks = [
            asyncio.create_task(self._guarded_delegation(validated, index, semaphore))
            for index in range(len(validated.delegations))
        ]
        try:
            results = tuple(await asyncio.gather(*tasks))
        except BaseException as original:
            for task in tasks:
                if not task.done():
                    task.cancel()
            settled = await asyncio.gather(*tasks, return_exceptions=True)
            persistence_failure = next(
                (
                    item
                    for item in settled
                    if isinstance(item, BrokerCancellationPersistenceError)
                ),
                None,
            )
            if persistence_failure is not None and not isinstance(
                original, BrokerCancellationPersistenceError
            ):
                raise persistence_failure from original
            raise
        result = GrokWorkerBrokerResult(
            plan_id=validated.plan_id,
            plan_digest=validated.plan_digest,
            supervision=validated.supervision,
            supervisor_plane=validated.supervisor_plane,
            supervisor_model=validated.supervisor_model,
            status=_global_status(results),
            delegations=results,
        )
        return result.validate_against_plan(validated)
