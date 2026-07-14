"""Strict provider-neutral contracts for external semantic model calls.

These types deliberately stop at normalized text inference.  They do not grant
routing, tool, effect, or verification authority.  Provider adapters translate
between these contracts and first-party HTTP APIs; the future broker decides
which adapter may be called.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_MESSAGE_CHARS = 128_000
MAX_REQUEST_CHARS = 500_000
MAX_RESPONSE_CHARS = 1_000_000
MAX_OUTPUT_TOKENS = 32_768
MAX_TIMEOUT_SECONDS = 120.0

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,191}$")
_SAFE_HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


class ProviderId(str, Enum):
    XAI = "xai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class ProviderChannel(str, Enum):
    XAI_API = "xai_api"
    GROK_CLI = "grok_cli"
    OPENAI_API = "openai_api"
    ANTHROPIC_API = "anthropic_api"
    GEMINI_API_KEY = "gemini_api_key"
    VERTEX_ADC = "vertex_adc"


class CredentialPlane(str, Enum):
    METERED_API = "metered_api"
    SUBSCRIPTION = "subscription"


class RouteClass(str, Enum):
    PLANNING = "planning"
    CODING = "coding"
    VISION = "vision"
    RESEARCH = "research"


class CredentialState(str, Enum):
    CONFIGURED = "configured"
    MISSING = "missing"
    DEFERRED = "deferred"


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GrokSupervisorBinding(StrictContract):
    """Opaque Grok-owned state copied into every worker receipt.

    Adapters may bind outputs to this state, but cannot create, extend, route,
    verify, harvest, or finalize it.
    """

    supervisor: Literal["grok"] = "grok"
    session_id: Annotated[str, Field(min_length=1, max_length=128)]
    objective_id: Annotated[str, Field(min_length=1, max_length=128)]
    route_decision_id: Annotated[str, Field(min_length=1, max_length=128)]
    ttl_expires_at: datetime

    @model_validator(mode="after")
    def validate_binding(self) -> "GrokSupervisorBinding":
        for value in (self.session_id, self.objective_id, self.route_decision_id):
            if not _SAFE_IDENTIFIER_RE.fullmatch(value):
                raise ValueError("supervisor identifiers must be opaque and safe")
        if self.ttl_expires_at.tzinfo is None:
            raise ValueError("ttl_expires_at must be timezone-aware")
        return self


class WorkerAuthority(StrictContract):
    """Mechanical denial of supervisor authority to external model workers."""

    role: Literal["subordinate_worker"] = "subordinate_worker"
    supervisor: Literal["grok"] = "grok"
    may_route: Literal[False] = False
    may_verify: Literal[False] = False
    may_harvest: Literal[False] = False
    may_finalize: Literal[False] = False


class ProviderMessage(StrictContract):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_CHARS)]


class ProviderRequest(StrictContract):
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    supervision: GrokSupervisorBinding
    route: RouteClass
    messages: Annotated[list[ProviderMessage], Field(min_length=1, max_length=100)]
    model: Annotated[str, Field(min_length=1, max_length=192)] | None = None
    max_output_tokens: Annotated[int, Field(ge=1, le=MAX_OUTPUT_TOKENS)] = 4096
    timeout_seconds: Annotated[float, Field(ge=1.0, le=MAX_TIMEOUT_SECONDS)] = 60.0
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "ProviderRequest":
        if not _SAFE_IDENTIFIER_RE.fullmatch(self.request_id):
            raise ValueError("request_id must be an opaque safe identifier")
        if self.model is not None and not _SAFE_MODEL_RE.fullmatch(self.model):
            raise ValueError("model must be a safe provider model identifier")
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("messages must contain at least one user turn")
        if sum(len(message.content) for message in self.messages) > MAX_REQUEST_CHARS:
            raise ValueError("combined message content exceeds the request bound")
        return self


def model_visible_messages(request: ProviderRequest) -> tuple[ProviderMessage, ...]:
    """Return the exact normalized messages shown to a subordinate worker.

    The supervisor deadline is model-visible and shared by every transport.
    Keeping this construction in the contract module lets the adapter, the
    attempt ledger, and the Grok broker layer hash the same logical request.
    """

    expires = (
        request.supervision.ttl_expires_at.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    ttl_fact = ProviderMessage(
        role="system",
        content=f"Supervisor TTL expires at {expires}; do not claim work after it.",
    )
    return (ttl_fact, *request.messages)


class ProviderAttemptStart(StrictContract):
    """Grok-authorized identity for one physical subordinate channel call."""

    version: Literal["provider-attempt-start/v1"] = "provider-attempt-start/v1"
    attempt_id: Annotated[str, Field(min_length=1, max_length=128)]
    delegation_id: Annotated[str, Field(min_length=1, max_length=128)]
    attempt_ordinal: Annotated[int, Field(ge=1, le=128)]
    supervisor_plane: Literal["CLI", "API"]
    supervisor_model: Annotated[str, Field(min_length=1, max_length=192)]
    provider: ProviderId
    channel: ProviderChannel
    credential_plane: CredentialPlane
    requested_model: Annotated[str, Field(min_length=1, max_length=192)]
    request: ProviderRequest

    @model_validator(mode="after")
    def validate_start(self) -> "ProviderAttemptStart":
        for value in (self.attempt_id, self.delegation_id):
            if not _SAFE_IDENTIFIER_RE.fullmatch(value):
                raise ValueError("attempt identifiers must be opaque and safe")
        for value in (self.supervisor_model, self.requested_model):
            if not _SAFE_MODEL_RE.fullmatch(value):
                raise ValueError("attempt model identifiers must be safe")
        if not self.supervisor_model.casefold().startswith("grok-"):
            raise ValueError("subordinate attempts require an exact Grok supervisor model")
        if self.provider == ProviderId.XAI:
            raise ValueError("xAI planes are supervisor attempts, not subordinate workers")
        if self.request.model is not None and self.request.model != self.requested_model:
            raise ValueError("explicit request model must match requested_model")
        allowed_channels = {
            ProviderId.OPENAI: {ProviderChannel.OPENAI_API},
            ProviderId.ANTHROPIC: {ProviderChannel.ANTHROPIC_API},
            ProviderId.GOOGLE: {
                ProviderChannel.GEMINI_API_KEY,
                ProviderChannel.VERTEX_ADC,
            },
        }
        if self.channel not in allowed_channels[self.provider]:
            raise ValueError("provider and physical channel do not match")
        expected_plane = {
            ProviderChannel.GROK_CLI: CredentialPlane.SUBSCRIPTION,
            ProviderChannel.XAI_API: CredentialPlane.METERED_API,
            ProviderChannel.OPENAI_API: CredentialPlane.METERED_API,
            ProviderChannel.ANTHROPIC_API: CredentialPlane.METERED_API,
            ProviderChannel.GEMINI_API_KEY: CredentialPlane.METERED_API,
            ProviderChannel.VERTEX_ADC: CredentialPlane.METERED_API,
        }[self.channel]
        if self.credential_plane != expected_plane:
            raise ValueError("channel and credential plane do not match")
        return self


class ProviderModelPins(StrictContract):
    planning: Annotated[str, Field(min_length=1, max_length=192)]
    coding: Annotated[str, Field(min_length=1, max_length=192)]
    vision: Annotated[str, Field(min_length=1, max_length=192)]
    research: Annotated[str, Field(min_length=1, max_length=192)]

    @model_validator(mode="after")
    def validate_model_names(self) -> "ProviderModelPins":
        for value in (self.planning, self.coding, self.vision, self.research):
            if not _SAFE_MODEL_RE.fullmatch(value):
                raise ValueError("model pins must be safe provider identifiers")
        return self

    def for_route(self, route: RouteClass) -> str:
        return str(getattr(self, route.value))


class ProviderDescriptor(StrictContract):
    provider: ProviderId
    channel: ProviderChannel
    credential_plane: CredentialPlane
    display_name: Annotated[str, Field(min_length=1, max_length=64)]
    endpoint_host: Annotated[str, Field(min_length=3, max_length=253)]
    endpoint_kind: Literal["first_party_api", "vertex_ai"]
    credential_kind: Literal["api_key", "google_adc"]
    credential_env_names: tuple[str, ...]
    credential_state: CredentialState
    models: ProviderModelPins
    supported_routes: tuple[RouteClass, ...] = (
        RouteClass.PLANNING,
        RouteClass.CODING,
        RouteClass.RESEARCH,
    )
    max_output_tokens: Annotated[int, Field(ge=1, le=MAX_OUTPUT_TOKENS)] = 16_384
    max_timeout_seconds: Annotated[float, Field(ge=1.0, le=MAX_TIMEOUT_SECONDS)] = 120.0
    data_handling: Literal["provider_managed", "project_policy"]
    residency: Annotated[str, Field(min_length=1, max_length=64)]
    supports_normalized_tools: bool = False
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def validate_descriptor(self) -> "ProviderDescriptor":
        if not _SAFE_HOST_RE.fullmatch(self.endpoint_host):
            raise ValueError("endpoint_host must be a fixed DNS hostname")
        if not self.supported_routes or len(set(self.supported_routes)) != len(
            self.supported_routes
        ):
            raise ValueError("supported routes must be nonempty and unique")
        if len(set(self.credential_env_names)) != len(self.credential_env_names):
            raise ValueError("credential environment names must be unique")
        for name in self.credential_env_names:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name):
                raise ValueError("credential environment names must be safe identifiers")
        return self


class ProviderTokenUsage(StrictContract):
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None
    source: Literal["provider_exact", "derived", "partial", "unavailable"] = (
        "unavailable"
    )

    @model_validator(mode="after")
    def validate_total(self) -> "ProviderTokenUsage":
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        present = tuple(value is not None for value in values)
        if self.source == "unavailable" and any(present):
            raise ValueError("unavailable usage cannot contain token counts")
        if self.source == "provider_exact" and not all(present):
            raise ValueError("exact usage requires every provider token field")
        if self.source == "derived" and not (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens == self.input_tokens + self.output_tokens
        ):
            raise ValueError("derived usage requires input and output with their sum")
        if self.source == "partial" and all(present):
            raise ValueError("partial usage cannot claim every count as valid")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens < self.input_tokens + self.output_tokens
        ):
            raise ValueError("token total cannot be smaller than its components")
        return self


class ProviderReceipt(StrictContract):
    version: Literal["provider-receipt/v1"] = "provider-receipt/v1"
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    supervision: GrokSupervisorBinding
    provider: ProviderId
    channel: ProviderChannel
    credential_plane: CredentialPlane
    route: RouteClass
    requested_model: Annotated[str, Field(min_length=1, max_length=192)]
    resolved_model: Annotated[str, Field(min_length=1, max_length=192)]
    model_source: Literal["provider_reported", "requested_fallback"]
    endpoint_host: Annotated[str, Field(min_length=3, max_length=253)]
    endpoint_kind: Literal["first_party_api", "vertex_ai"]
    credential_kind: Literal["api_key", "google_adc"]
    billing_class: Literal["metered"] = "metered"
    cost_usd: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=8)] | None = None
    cost_source: Literal["provider_exact", "locally_computed", "unavailable"] = (
        "unavailable"
    )
    region: Annotated[str, Field(min_length=1, max_length=64)]
    account_fingerprint: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{12}$")] | None = None
    response_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    duration_ms: Annotated[int, Field(ge=0, le=3_600_000)]
    usage: ProviderTokenUsage
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def validate_receipt(self) -> "ProviderReceipt":
        if not _SAFE_IDENTIFIER_RE.fullmatch(self.request_id):
            raise ValueError("receipt request_id must be a safe identifier")
        if not _SAFE_MODEL_RE.fullmatch(self.requested_model):
            raise ValueError("receipt requested_model must be safe")
        if not _SAFE_MODEL_RE.fullmatch(self.resolved_model):
            raise ValueError("receipt resolved_model must be safe")
        if not _SAFE_HOST_RE.fullmatch(self.endpoint_host):
            raise ValueError("receipt endpoint_host must be a fixed hostname")
        if self.response_id and not _SAFE_IDENTIFIER_RE.fullmatch(self.response_id):
            raise ValueError("receipt response_id must be a safe identifier")
        if (self.cost_usd is None) != (self.cost_source == "unavailable"):
            raise ValueError("receipt cost and cost source must agree")
        return self


class ProviderResponse(StrictContract):
    provider: ProviderId
    channel: ProviderChannel
    model: Annotated[str, Field(min_length=1, max_length=192)]
    text: Annotated[str, Field(max_length=MAX_RESPONSE_CHARS)]
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "unknown"]
    receipt: ProviderReceipt
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def bind_receipt(self) -> "ProviderResponse":
        if self.provider != self.receipt.provider:
            raise ValueError("response provider does not match its receipt")
        if self.channel != self.receipt.channel:
            raise ValueError("response channel does not match its receipt")
        if self.model != self.receipt.resolved_model:
            raise ValueError("response model does not match its receipt")
        return self


class ProviderFailureReceipt(StrictContract):
    """Bounded, secret-safe failure evidence returned to the Grok supervisor."""

    version: Literal["provider-failure/v1"] = "provider-failure/v1"
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    supervision: GrokSupervisorBinding
    provider: ProviderId
    channel: ProviderChannel
    credential_plane: CredentialPlane
    route: RouteClass
    requested_model: Annotated[str, Field(min_length=1, max_length=192)]
    endpoint_host: Annotated[str, Field(min_length=3, max_length=253)]
    error_kind: Literal["configuration", "transport", "protocol", "internal"]
    error_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    duration_ms: Annotated[int, Field(ge=0, le=3_600_000)]
    usage: ProviderTokenUsage = ProviderTokenUsage()
    cost_usd: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=8)] | None = None
    cost_source: Literal["provider_exact", "locally_computed", "unavailable"] = (
        "unavailable"
    )
    authority: WorkerAuthority = WorkerAuthority()

    @model_validator(mode="after")
    def validate_failure(self) -> "ProviderFailureReceipt":
        if not _SAFE_IDENTIFIER_RE.fullmatch(self.request_id):
            raise ValueError("failure request_id must be safe")
        if not _SAFE_MODEL_RE.fullmatch(self.requested_model):
            raise ValueError("failure requested_model must be safe")
        if not _SAFE_HOST_RE.fullmatch(self.endpoint_host):
            raise ValueError("failure endpoint_host must be fixed")
        if (self.cost_usd is None) != (self.cost_source == "unavailable"):
            raise ValueError("failure cost and cost source must agree")
        return self


class ProviderAttemptResult(StrictContract):
    """One subordinate worker return or failure for Grok synthesis."""

    version: Literal["provider-attempt/v1"] = "provider-attempt/v1"
    status: Literal["returned", "failed"]
    response: ProviderResponse | None = None
    failure: ProviderFailureReceipt | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "ProviderAttemptResult":
        if self.status == "returned" and (self.response is None or self.failure is not None):
            raise ValueError("returned attempts require only a response")
        if self.status == "failed" and (self.failure is None or self.response is not None):
            raise ValueError("failed attempts require only a failure receipt")
        return self


@runtime_checkable
class ProviderAdapter(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...

    async def attempt(self, request: ProviderRequest) -> ProviderAttemptResult: ...


def is_safe_model_id(value: str) -> bool:
    """Return whether a provider-supplied model ID is safe to put in a receipt."""

    return bool(_SAFE_MODEL_RE.fullmatch(str(value or "")))


def is_safe_response_id(value: str) -> bool:
    """Return whether an upstream response ID is safe to put in a receipt."""

    return bool(_SAFE_IDENTIFIER_RE.fullmatch(str(value or "")))
