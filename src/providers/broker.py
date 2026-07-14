"""Internal Grok-supervised broker for bounded subordinate model evidence.

This module is deliberately not wired to the public MCP server or routing
stack.  A :class:`GrokDelegationPlan` is a strict transport contract, not proof
that Grok created it.  Only a future trusted Grok runtime may mint and execute
plans at the integration boundary.

The broker chooses physical credential channels from an injected registry,
records every physical attempt before its effect, and returns durable transport
evidence to Grok without synthesizing or granting semantic authority.  It never
reads credentials and never constructs a cloud harvester.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import time
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, model_validator

from .contracts import (
    MAX_OUTPUT_TOKENS,
    MAX_REQUEST_CHARS,
    MAX_TIMEOUT_SECONDS,
    CredentialPlane,
    CredentialState,
    GrokSupervisorBinding,
    ProviderAdapter,
    ProviderAttemptResult,
    ProviderAttemptStart,
    ProviderChannel,
    ProviderDescriptor,
    ProviderFailureReceipt,
    ProviderId,
    ProviderMessage,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    RouteClass,
    StrictContract,
    WorkerAuthority,
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


class GrokWorkerDelegation(StrictContract):
    """One semantic worker request chosen by the Grok supervisor.

    Physical channels are intentionally absent.  The broker applies the fixed
    same-provider channel ladder from its injected registry.
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
    max_output_tokens: Annotated[int, Field(ge=1, le=MAX_OUTPUT_TOKENS)] = 4096
    timeout_seconds: Annotated[float, Field(ge=1.0, le=MAX_TIMEOUT_SECONDS)] = 60.0
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None

    @model_validator(mode="after")
    def require_user_message(self) -> "GrokWorkerDelegation":
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("delegation messages require a user turn")
        if sum(len(message.content) for message in self.messages) > MAX_REQUEST_CHARS:
            raise ValueError("combined delegation content exceeds the request bound")
        return self


class GrokDelegationPlan(StrictContract):
    """Content-addressed internal plan bound to one exact Grok turn.

    ``supervisor='grok'`` and a Grok-shaped model ID are validation constraints,
    not authentication.  The future runtime integration must accept plans only
    from its trusted Grok session state, never directly from an MCP caller.
    """

    version: Literal["grok-delegation-plan/v1"] = "grok-delegation-plan/v1"
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
        if self.persistence in {"begin_failed", "terminal_indeterminate"} and (
            self.harvest.status != "not_applicable"
        ):
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
        returned = [
            attempt
            for attempt in self.attempts
            if attempt.result is not None and attempt.result.status == "returned"
        ]
        if (self.status == "returned") != (len(returned) == 1):
            raise ValueError(
                "returned delegation status must identify one durable return"
            )
        if (
            self.status == "indeterminate"
            and self.attempts
            and not any(
                attempt.persistence.endswith("indeterminate")
                or attempt.persistence == "begin_failed"
                for attempt in self.attempts
            )
        ):
            raise ValueError("indeterminate status requires persistence evidence")
        return self


class GrokWorkerBrokerResult(StrictContract):
    version: Literal["grok-worker-broker-result/v1"] = "grok-worker-broker-result/v1"
    plan_id: Annotated[str, Field(pattern=r"^gdp:[0-9a-f]{64}$")]
    plan_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    supervision: GrokSupervisorBinding
    supervisor_plane: Literal["CLI", "API"]
    supervisor_model: Annotated[str, Field(min_length=1, max_length=192)]
    status: Literal["returned", "mixed", "failed", "indeterminate", "expired"]
    delegations: Annotated[
        tuple[BrokerDelegationResult, ...],
        Field(min_length=1, max_length=MAX_DELEGATIONS),
    ]
    semantic_outcome: Literal["unverified"] = "unverified"
    synthesized: Literal[False] = False
    authority: WorkerAuthority = WorkerAuthority()


class ProviderAttemptStore(Protocol):
    async def begin_provider_attempt(self, start: Any) -> bool: ...

    async def complete_provider_attempt(self, attempt_id: str, result: Any) -> bool: ...

    async def list_provider_attempts(
        self,
        supervisor_session_id: str | None = None,
        delegation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


class ProviderAttemptHarvestTrigger(Protocol):
    async def run_once(self, store: Any) -> Any: ...


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


def _execution_contract_digest(
    descriptor: ProviderDescriptor,
    *,
    route: RouteClass,
    requested_model: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> str:
    """Hash effect semantics while excluding mutable availability metadata."""

    payload = {
        "provider": descriptor.provider.value,
        "channel": descriptor.channel.value,
        "credential_plane": descriptor.credential_plane.value,
        "endpoint_host": descriptor.endpoint_host,
        "endpoint_kind": descriptor.endpoint_kind,
        "credential_kind": descriptor.credential_kind,
        "billing_class": descriptor.billing_class,
        "client_identity": descriptor.client_identity,
        "route": route.value,
        "requested_model": requested_model,
        "max_output_tokens": max_output_tokens,
        "timeout_seconds": timeout_seconds,
        "data_handling": descriptor.data_handling,
        "residency": descriptor.residency,
        "supports_normalized_tools": descriptor.supports_normalized_tools,
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
        registry: Mapping[ProviderChannel, ProviderAdapter],
        store: ProviderAttemptStore,
        harvester: ProviderAttemptHarvestTrigger | None = None,
        clock: Any | None = None,
        harvest_timeout_seconds: float = 5.0,
        terminal_write_timeout_seconds: float = 2.0,
    ) -> None:
        if not 0.1 <= float(harvest_timeout_seconds) <= 30.0:
            raise ValueError("harvest trigger timeout is out of bounds")
        if not 0.1 <= float(terminal_write_timeout_seconds) <= 5.0:
            raise ValueError("terminal write timeout is out of bounds")
        self._registry = dict(registry)
        self._store = store
        self._harvester = harvester
        self._clock = clock or (lambda: datetime.now(UTC))
        self._harvest_timeout_seconds = float(harvest_timeout_seconds)
        self._terminal_write_timeout_seconds = float(terminal_write_timeout_seconds)
        self._harvest_lock = asyncio.Lock()
        self._validate_registry()

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("broker clock must return a timezone-aware datetime")
        return now

    def _remaining(self, plan: GrokDelegationPlan) -> float:
        return (plan.supervision.ttl_expires_at - self._now()).total_seconds()

    def _validate_registry(self) -> None:
        descriptor_channels: set[ProviderChannel] = set()
        for channel, adapter in self._registry.items():
            if channel not in _ALL_WORKER_CHANNELS:
                raise ValueError("broker registry contains a supervisor channel")
            descriptor = adapter.descriptor
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

    def _select_channel(
        self,
        *,
        provider: ProviderId,
        route: RouteClass,
        plane: CredentialPlane,
    ) -> tuple[ProviderChannel, ProviderAdapter, ProviderDescriptor] | None:
        ladder = (
            _SUBSCRIPTION_LADDERS[provider]
            if plane == CredentialPlane.SUBSCRIPTION
            else _API_LADDERS[provider]
        )
        for channel in ladder:
            adapter = self._registry.get(channel)
            if adapter is None:
                continue
            descriptor = adapter.descriptor
            if (
                descriptor.channel != channel
                or descriptor.provider != provider
                or descriptor.credential_plane != plane
            ):
                raise ValueError("adapter descriptor changed after registry validation")
            if descriptor.credential_state == CredentialState.MISSING:
                continue
            if route not in descriptor.supported_routes:
                continue
            return channel, adapter, descriptor
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
        requested_model = descriptor.models.for_route(delegation.route)
        effective_output_tokens = min(
            delegation.max_output_tokens,
            descriptor.max_output_tokens,
        )
        effective_timeout_seconds = min(
            delegation.timeout_seconds,
            descriptor.max_timeout_seconds,
        )
        descriptor_digest = _execution_contract_digest(
            descriptor,
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
            messages=list(delegation.messages),
            model=requested_model,
            max_output_tokens=effective_output_tokens,
            timeout_seconds=effective_timeout_seconds,
            temperature=delegation.temperature,
        )
        return ProviderAttemptStart(
            attempt_id=attempt_id,
            delegation_id=delegation_id,
            attempt_ordinal=ordinal,
            supervisor_plane=plan.supervisor_plane,
            supervisor_model=plan.supervisor_model,
            provider=delegation.provider,
            channel=channel,
            credential_plane=descriptor.credential_plane,
            requested_model=requested_model,
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
    def _result_matches_start(
        result: ProviderAttemptResult,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
    ) -> bool:
        receipt = (
            result.response.receipt if result.response is not None else result.failure
        )
        if receipt is None:
            return False
        expected = (
            receipt.request_id == start.request.request_id,
            receipt.supervision == start.request.supervision,
            receipt.provider == start.provider == descriptor.provider,
            receipt.channel == start.channel == descriptor.channel,
            receipt.credential_plane
            == start.credential_plane
            == descriptor.credential_plane,
            receipt.route == start.request.route,
            receipt.requested_model == start.requested_model,
            receipt.endpoint_host == descriptor.endpoint_host,
            receipt.endpoint_kind == descriptor.endpoint_kind,
            receipt.credential_kind == descriptor.credential_kind,
            receipt.billing_class == descriptor.billing_class,
            receipt.client_identity == descriptor.client_identity,
            receipt.authority == WorkerAuthority(),
        )
        return all(expected)

    async def _invoke_adapter(
        self,
        *,
        plan: GrokDelegationPlan,
        adapter: ProviderAdapter,
        descriptor: ProviderDescriptor,
        start: ProviderAttemptStart,
    ) -> ProviderAttemptResult:
        started = time.monotonic()
        remaining = self._remaining(plan)
        if remaining <= 0:
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code="ttl_expired",
                duration_ms=0,
            )
        physical_timeout = min(
            remaining,
            start.request.timeout_seconds,
            descriptor.max_timeout_seconds,
        )
        timeout_code = (
            "ttl_expired"
            if remaining
            <= min(start.request.timeout_seconds, descriptor.max_timeout_seconds)
            else "timeout"
        )
        try:
            async with asyncio.timeout(physical_timeout):
                raw_result = await adapter.attempt(start.request)
            result = (
                raw_result
                if isinstance(raw_result, ProviderAttemptResult)
                else ProviderAttemptResult.model_validate(raw_result)
            )
        except TimeoutError:
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code=timeout_code,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception:
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="internal",
                error_code="unexpected_adapter_exception",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        if self._remaining(plan) <= 0:
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code="late_result_rejected",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        if not self._result_matches_start(result, start, descriptor):
            return self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="protocol",
                error_code="adapter_contract_mismatch",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        return result

    async def _stored_result(
        self,
        start: ProviderAttemptStart,
        descriptor: ProviderDescriptor,
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
        if not self._result_matches_start(result, start, descriptor):
            return None, "replay_contract_mismatch"
        return result, None

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
                    raw = await self._harvester.run_once(self._store)
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

    @staticmethod
    async def _stop_write_task(write: asyncio.Task[bool]) -> None:
        if write.done():
            return
        write.cancel()
        done, _ = await asyncio.wait({write}, timeout=0.1)
        for task in done:
            try:
                task.result()
            except BaseException:
                pass

    async def _write_terminal(
        self,
        *,
        start: ProviderAttemptStart,
        result: ProviderAttemptResult,
        descriptor: ProviderDescriptor,
    ) -> bool:
        """Bound and shield only the authoritative terminal ledger write.

        Cancellation of the broker task does not cancel a write that already
        received the provider result.  After the bounded write is confirmed,
        the original cancellation still propagates to the caller.
        """

        write = asyncio.create_task(
            self._store.complete_provider_attempt(start.attempt_id, result)
        )
        try:
            stored = await asyncio.wait_for(
                asyncio.shield(write),
                timeout=self._terminal_write_timeout_seconds,
            )
        except asyncio.CancelledError:
            try:
                stored = await asyncio.wait_for(
                    asyncio.shield(write),
                    timeout=self._terminal_write_timeout_seconds,
                )
                if not stored:
                    replay, _ = await self._stored_result(start, descriptor)
                    if replay != result:
                        raise ValueError("cancelled terminal replay does not match")
            except BaseException as exc:
                await self._stop_write_task(write)
                raise BrokerCancellationPersistenceError(
                    "cancelled provider attempt could not be terminalized"
                ) from exc
            raise
        except BaseException:
            await self._stop_write_task(write)
            raise
        return stored

    async def _attempt(
        self,
        *,
        plan: GrokDelegationPlan,
        index: int,
        channel: ProviderChannel,
        adapter: ProviderAdapter,
        descriptor: ProviderDescriptor,
        ordinal: Literal[1, 2],
    ) -> BrokerAttemptEvidence:
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
                stored_result, error = await self._stored_result(start, descriptor)
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
                harvest=await self._harvest(plan),
            )

        try:
            result = await self._invoke_adapter(
                plan=plan,
                adapter=adapter,
                descriptor=descriptor,
                start=start,
            )
        except asyncio.CancelledError:
            result = self._normalized_failure(
                start=start,
                descriptor=descriptor,
                error_kind="transport",
                error_code="broker_cancelled",
                duration_ms=0,
            )
            try:
                await self._write_terminal(
                    start=start,
                    result=result,
                    descriptor=descriptor,
                )
            except BrokerCancellationPersistenceError:
                raise
            except BaseException as exc:
                raise BrokerCancellationPersistenceError(
                    "cancelled provider attempt could not be terminalized"
                ) from exc
            raise
        try:
            stored = await self._write_terminal(
                start=start,
                result=result,
                descriptor=descriptor,
            )
            if not stored:
                replay, _ = await self._stored_result(start, descriptor)
                if replay != result:
                    raise ValueError("terminal replay does not match result")
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
                    provider=delegation.provider,
                    route=delegation.route,
                    plane=CredentialPlane.SUBSCRIPTION,
                )
                api = (
                    self._select_channel(
                        provider=delegation.provider,
                        route=delegation.route,
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
                channel, adapter, descriptor = subscription
                evidence = await self._attempt(
                    plan=plan,
                    index=index,
                    channel=channel,
                    adapter=adapter,
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
            channel, adapter, descriptor = api
            evidence = await self._attempt(
                plan=plan,
                index=index,
                channel=channel,
                adapter=adapter,
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

        validated = (
            plan
            if isinstance(plan, GrokDelegationPlan)
            else GrokDelegationPlan.model_validate(plan)
        )
        self._now()
        semaphore = asyncio.Semaphore(validated.max_concurrency)
        results = tuple(
            await asyncio.gather(
                *(
                    self._guarded_delegation(validated, index, semaphore)
                    for index in range(len(validated.delegations))
                )
            )
        )
        return GrokWorkerBrokerResult(
            plan_id=validated.plan_id,
            plan_digest=validated.plan_digest,
            supervision=validated.supervision,
            supervisor_plane=validated.supervisor_plane,
            supervisor_model=validated.supervisor_model,
            status=_global_status(results),
            delegations=results,
        )
