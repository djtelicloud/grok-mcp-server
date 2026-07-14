"""Authenticated, request-scoped MCP client-sampling bridge.

This module is deliberately inert.  It does not register a tool, change the
HTTP server to stateful mode, construct a broker, or discover subscription
providers.  A future stateful HTTP integration injects only an immutable
session authorization and shared runtime gate.  Later, Grok policy may mint a
one-delegation provider/model grant for the exact supervisor, provider request,
route, and MCP tool request and place it in the current Starlette request
scope.  Only then can a tool handler open a short-lived lease around the exact
FastMCP request context.

The lease is the lifetime boundary: its callback cannot be cached globally,
survive the tool request, cross principals or MCP sessions, select a provider,
use tools, or grant a worker final authority.

This is not yet broker-wirable.  Planning needs a stable session-capability
descriptor before a deterministic provider request ID exists.  Only after the
attempt start is durably recorded may later integration mint the exact grant,
materialize this lease-owned adapter, verify lane conformance, and teach the
broker's outer timeout to consult ``adapter.effect_claimed()``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import mcp.types as mcp_types
from pydantic import Field, field_validator, model_validator
from starlette.requests import Request

from .base import Clock
from .contracts import (
    MAX_RESPONSE_CHARS,
    GrokSupervisorBinding,
    ProviderChannel,
    ProviderId,
    ProviderModelPins,
    ProviderRequest,
    RouteClass,
    StrictContract,
)
from .errors import (
    ProviderAuthorizationInvariantError,
    ProviderConfigurationError,
    ProviderError,
    ProviderProtocolError,
    ProviderTransportError,
)
from .subscription import (
    ClientSamplingRequest,
    ClientSamplingResult,
    MCPClientSamplingAdapter,
    SamplingCapability,
    SamplingTextContent,
    _create_sealed_mcp_sampling_adapter,
    _create_sealed_sampling_client_binding,
    provider_request_digest,
)


MCP_SESSION_AUTHORIZATION_SCOPE_KEY = "unigrok.mcp_sampling.session_authorization"
MCP_PROVIDER_GRANTS_SCOPE_KEY = "unigrok.mcp_sampling.provider_grants"
MCP_SESSION_RUNTIME_SCOPE_KEY = "unigrok.mcp_sampling.session_runtime"

_SAFE_BINDING_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SAMPLING_CHANNEL_PROVIDERS: dict[ProviderChannel, ProviderId] = {
    ProviderChannel.OPENAI_MCP_SAMPLING: ProviderId.OPENAI,
    ProviderChannel.ANTHROPIC_MCP_SAMPLING: ProviderId.ANTHROPIC,
    ProviderChannel.GOOGLE_MCP_SAMPLING: ProviderId.GOOGLE,
}
MAX_MCP_SAMPLING_EFFECT_CLAIMS = 1024


def _canonical_digest(namespace: str, payload: Mapping[str, Any]) -> str:
    material = json.dumps(
        {"namespace": namespace, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _snapshot(model_type, value):
    """Revalidate even objects made with Pydantic's non-validating helpers."""

    if not isinstance(value, model_type):
        raise TypeError(f"expected {model_type.__name__}")
    return model_type.model_validate_json(value.model_dump_json())


def _validate_visible_identity(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("identity fields cannot contain control characters")
    return value


def _scope_header(scope: Mapping[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            try:
                return value.decode("latin-1")
            except (AttributeError, UnicodeError):
                return None
    return None


class MCPSessionAuthorization(StrictContract):
    """Gateway-issued authorization for one principal-bound MCP session.

    ``verified_local`` covers a directly verified loopback request or a
    separately attested loopback-only proxy boundary.  Anonymous HTTP is only
    legal inside that boundary.  Remote sessions must have an authenticated
    principal.  This object is not accepted from request JSON or headers; a
    future stateful session middleware must inject it into the ASGI scope.
    """

    version: Literal["mcp-sampling-session-authorization/v1"] = (
        "mcp-sampling-session-authorization/v1"
    )
    binding_id: Annotated[str, Field(pattern=_SAFE_BINDING_PATTERN)]
    mcp_session_id: Annotated[str, Field(pattern=_SAFE_BINDING_PATTERN)]
    principal: Annotated[str, Field(min_length=1, max_length=240, repr=False)]
    client_label: Annotated[str, Field(min_length=1, max_length=128, repr=False)]
    mcp_client_name: Annotated[str, Field(min_length=1, max_length=128, repr=False)]
    trust: Literal["verified_local", "authenticated_remote"]
    issued_at: datetime
    expires_at: datetime

    @field_validator("principal", "client_label", "mcp_client_name")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        return _validate_visible_identity(value)

    @model_validator(mode="after")
    def validate_authorization(self) -> "MCPSessionAuthorization":
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("session authorization timestamps must be timezone-aware")
        if self.issued_at >= self.expires_at:
            raise ValueError("session authorization must expire after issuance")
        if self.principal == "http:anon" and self.trust != "verified_local":
            raise ValueError("anonymous MCP sampling requires verified local trust")
        if self.trust == "authenticated_remote" and self.principal == "http:anon":
            raise ValueError("remote MCP sampling requires an authenticated principal")
        return self

    @property
    def authorization_digest(self) -> str:
        return _canonical_digest(
            "mcp_sampling_session_authorization",
            self.model_dump(mode="json"),
        )


class TrustedMCPProviderGrant(StrictContract):
    """Server-owned provider/model grant bound to one authorized MCP session.

    Client labels and advertised sampling support never imply a provider
    brand.  Only later Grok routing policy may issue this one-delegation grant;
    generic session middleware must never mint it.
    """

    version: Literal["trusted-mcp-provider-grant/v1"] = "trusted-mcp-provider-grant/v1"
    issuer: Literal["grok"] = "grok"
    grant_id: Annotated[str, Field(pattern=_SAFE_BINDING_PATTERN)]
    session_authorization_digest: Annotated[str, Field(pattern=_DIGEST_PATTERN)]
    supervision: GrokSupervisorBinding
    provider_request_id: Annotated[str, Field(pattern=_SAFE_BINDING_PATTERN)]
    provider_request_digest: Annotated[str, Field(pattern=_DIGEST_PATTERN)]
    mcp_related_request_id: str | int
    provider: ProviderId
    channel: ProviderChannel
    route: RouteClass
    models: ProviderModelPins
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_grant(self) -> "TrustedMCPProviderGrant":
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("provider grant timestamps must be timezone-aware")
        if self.issued_at >= self.expires_at:
            raise ValueError("provider grant must expire after issuance")
        if _SAMPLING_CHANNEL_PROVIDERS.get(self.channel) != self.provider:
            raise ValueError("provider grant channel does not match provider")
        if isinstance(self.mcp_related_request_id, str):
            if (
                not self.mcp_related_request_id
                or len(self.mcp_related_request_id) > 128
            ):
                raise ValueError("MCP related request id must be bounded")
            _validate_visible_identity(self.mcp_related_request_id)
        elif isinstance(self.mcp_related_request_id, bool):
            raise ValueError("MCP related request id cannot be boolean")
        if self.expires_at > self.supervision.ttl_expires_at:
            raise ValueError("provider grant cannot outlive Grok supervision")
        return self

    @property
    def grant_digest(self) -> str:
        return _canonical_digest(
            "trusted_mcp_provider_grant",
            self.model_dump(mode="json"),
        )

    @property
    def effect_digest(self) -> str:
        """Stable one-shot identity for the exact broker-authorized effect.

        Grant IDs and validity windows are intentionally excluded.  Reissuing
        a grant cannot repeat an indeterminate physical effect; an authorized
        retry requires a new deterministic ``ProviderRequest.request_id``.
        """

        return _canonical_digest(
            "trusted_mcp_sampling_effect",
            {
                "session_authorization_digest": self.session_authorization_digest,
                "provider": self.provider.value,
                "channel": self.channel.value,
                "provider_request_id": self.provider_request_id,
            },
        )

    @property
    def opaque_client_identity(self) -> str:
        # Deliberately includes the grant and therefore the authorized session,
        # principal, physical MCP session, provider, and model pins without
        # exposing any of those raw values in descriptors or receipts.
        return "mcp-" + self.grant_digest.removeprefix("sha256:")


class MCPSamplingSessionRuntime:
    """Mutable session-wide revocation and concurrency gate.

    A future authoritative stateful-session registry creates exactly one of
    these objects for one ``MCPSessionAuthorization`` and injects that same
    object into every request scope for the MCP session.  It is intentionally
    not a module global.  Separate request leases therefore share one physical
    sampling slot and one disconnect/revocation state.
    """

    def __init__(self, authorization: MCPSessionAuthorization) -> None:
        snapshot = _snapshot(MCPSessionAuthorization, authorization)
        self._authorization_digest = snapshot.authorization_digest
        self._mcp_session_id = snapshot.mcp_session_id
        self._binding_id = snapshot.binding_id
        self._semaphore = asyncio.Semaphore(1)
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._claimed_effects: set[str] = set()
        self._revoked = False

    @property
    def authorization_digest(self) -> str:
        return self._authorization_digest

    @property
    def mcp_session_id(self) -> str:
        return self._mcp_session_id

    @property
    def binding_id(self) -> str:
        return self._binding_id

    @property
    def revoked(self) -> bool:
        return self._revoked

    def register(self, task: asyncio.Task[Any]) -> None:
        if self._revoked:
            raise RuntimeError("session runtime is revoked")
        self._active_tasks.add(task)

    def unregister(self, task: asyncio.Task[Any]) -> None:
        self._active_tasks.discard(task)

    def sampling_slot(self) -> asyncio.Semaphore:
        return self._semaphore

    @staticmethod
    def _validate_effect_digest(effect_digest: str) -> None:
        if (
            not isinstance(effect_digest, str)
            or len(effect_digest) != 71
            or not effect_digest.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in effect_digest[7:])
        ):
            raise ValueError("sampling effect digest must be a SHA-256 identity")

    def effect_claimed(self, effect_digest: str) -> bool:
        """Return exact session-owned state for one stable effect identity."""

        self._validate_effect_digest(effect_digest)
        return effect_digest in self._claimed_effects

    def claim_effect(self, effect_digest: str) -> bool:
        """Atomically consume one grant before its physical sampling effect.

        This runtime belongs to one asyncio session loop.  The synchronous
        check-and-add has no suspension point and is therefore atomic with
        respect to every lease sharing that runtime.  Claims are terminal:
        revocation, timeout, disconnect, and indeterminate provider outcomes
        never remove them.
        """

        self._validate_effect_digest(effect_digest)
        if self._revoked:
            raise RuntimeError("session runtime is revoked")
        if effect_digest in self._claimed_effects:
            return False
        if len(self._claimed_effects) >= MAX_MCP_SAMPLING_EFFECT_CLAIMS:
            # Never evict: eviction would re-enable replay.  Revoke this
            # session before insertion and fail the new pre-effect claim.
            self._revoked = True
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            for task in tuple(self._active_tasks):
                if task is not current:
                    task.cancel()
            raise RuntimeError("session runtime effect claim capacity exhausted")
        self._claimed_effects.add(effect_digest)
        return True

    async def revoke(self, *, drain_timeout_seconds: float = 1.0) -> None:
        """Revoke the whole MCP session and boundedly cancel every sample."""

        if (
            not isinstance(drain_timeout_seconds, (int, float))
            or isinstance(drain_timeout_seconds, bool)
            or not math.isfinite(float(drain_timeout_seconds))
            or drain_timeout_seconds <= 0
            or drain_timeout_seconds > 10
        ):
            raise ValueError("session runtime drain timeout must be in (0, 10]")
        self._revoked = True
        current = asyncio.current_task()
        tasks = tuple(task for task in self._active_tasks if task is not current)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                async with asyncio.timeout(drain_timeout_seconds):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                pass


class StatefulMCPSamplingLease:
    """Short-lived callback lease around one exact FastMCP tool request."""

    def __init__(
        self,
        *,
        ctx: Any,
        request: Request,
        session: Any,
        related_request_id: str | int,
        authorization: MCPSessionAuthorization,
        raw_authorization: MCPSessionAuthorization,
        grant: TrustedMCPProviderGrant,
        raw_grant: TrustedMCPProviderGrant,
        runtime: MCPSamplingSessionRuntime,
        provider: ProviderId,
        channel: ProviderChannel,
        deadline: datetime,
        drain_timeout_seconds: float,
        clock: Clock,
    ) -> None:
        self._ctx = ctx
        self._request = request
        self._session = session
        self._related_request_id = related_request_id
        self._authorization = authorization
        self._raw_authorization = raw_authorization
        self._grant = grant
        self._raw_grant = raw_grant
        self._runtime = runtime
        self._provider = provider
        self._channel = channel
        self._deadline = deadline
        self._clock = clock
        self._drain_timeout_seconds = drain_timeout_seconds
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._entered = False
        self._revoked = False
        binding = _create_sealed_sampling_client_binding(
            capability=SamplingCapability(
                client_id=grant.opaque_client_identity,
                sampling=True,
            ),
            callback=self._sample,
            provider=grant.provider,
            channel=grant.channel,
            models=grant.models,
            binding_digest=grant.grant_digest,
            supervision=grant.supervision,
            provider_request_id=grant.provider_request_id,
            provider_request_digest=grant.provider_request_digest,
            route=grant.route,
            effect_claimed=lambda: runtime.effect_claimed(grant.effect_digest),
        )
        # Construct and snapshot the authoritative adapter before the internal
        # callback-bearing binding can escape.  Runtime callers receive only
        # this lease-owned adapter while the lease is active.
        self._adapter = _create_sealed_mcp_sampling_adapter(
            provider=provider,
            channel=channel,
            binding=binding,
            clock=clock,
        )

    async def __aenter__(self) -> "StatefulMCPSamplingLease":
        if self._entered or self._revoked:
            raise ProviderConfigurationError(self._provider, "sampling_lease_inactive")
        self._validate_live_binding()
        self._remaining_seconds()
        self._entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.revoke()

    @property
    def adapter(self) -> MCPClientSamplingAdapter:
        if not self._entered or self._revoked:
            raise ProviderConfigurationError(self._provider, "sampling_lease_inactive")
        return self._adapter

    async def revoke(self) -> None:
        """Revoke the callback, cancel in-flight samples, and bound the drain."""

        if self._revoked:
            return
        self._revoked = True
        current = asyncio.current_task()
        tasks = tuple(task for task in self._active_tasks if task is not current)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                async with asyncio.timeout(self._drain_timeout_seconds):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                # The lease remains revoked even if a non-cooperative client
                # transport ignores cancellation.  No result can be accepted.
                pass
        self._entered = False

    def _remaining_seconds(self) -> float:
        now = self._clock()
        if now.tzinfo is None:
            raise ProviderConfigurationError(self._provider, "clock_not_timezone_aware")
        effective_deadline = min(
            self._deadline,
            self._authorization.expires_at,
            self._grant.expires_at,
        )
        if now < self._authorization.issued_at or now < self._grant.issued_at:
            raise ProviderConfigurationError(
                self._provider, "sampling_authority_not_yet_valid"
            )
        remaining = (effective_deadline - now).total_seconds()
        if remaining <= 0:
            raise ProviderConfigurationError(self._provider, "ttl_expired")
        return remaining

    def _validate_live_binding(self) -> None:
        if not self._entered and self._revoked:
            raise ProviderConfigurationError(self._provider, "sampling_lease_inactive")
        try:
            request_context = self._ctx.request_context
            current_request = request_context.request
            current_session = request_context.session
            current_request_id = request_context.request_id
            exposed_session = self._ctx.session
        except Exception:
            raise ProviderConfigurationError(
                self._provider, "sampling_context_unavailable"
            ) from None
        if (
            current_request is not self._request
            or current_session is not self._session
            or exposed_session is not self._session
            or current_request_id != self._related_request_id
        ):
            raise ProviderConfigurationError(
                self._provider, "sampling_session_mismatch"
            )
        scope = self._request.scope
        if (
            scope.get(MCP_SESSION_AUTHORIZATION_SCOPE_KEY)
            is not self._raw_authorization
        ):
            raise ProviderConfigurationError(
                self._provider, "sampling_authorization_mismatch"
            )
        grants = scope.get(MCP_PROVIDER_GRANTS_SCOPE_KEY)
        if not isinstance(grants, tuple) or not any(
            item is self._raw_grant for item in grants
        ):
            raise ProviderConfigurationError(self._provider, "sampling_grant_mismatch")
        if scope.get(MCP_SESSION_RUNTIME_SCOPE_KEY) is not self._runtime:
            raise ProviderConfigurationError(
                self._provider, "sampling_session_mismatch"
            )
        try:
            current_authorization = _snapshot(
                MCPSessionAuthorization, self._raw_authorization
            )
            current_grant = _snapshot(TrustedMCPProviderGrant, self._raw_grant)
        except (TypeError, ValueError):
            raise ProviderConfigurationError(
                self._provider, "sampling_authorization_mismatch"
            ) from None
        if current_authorization != self._authorization:
            raise ProviderConfigurationError(
                self._provider, "sampling_authorization_mismatch"
            )
        if current_grant != self._grant:
            raise ProviderConfigurationError(self._provider, "sampling_grant_mismatch")
        if (
            _scope_header(scope, b"mcp-session-id")
            != self._authorization.mcp_session_id
        ):
            raise ProviderConfigurationError(
                self._provider, "sampling_session_mismatch"
            )
        if _scope_header(scope, b"x-client-id") != self._authorization.client_label:
            raise ProviderConfigurationError(self._provider, "sampling_client_mismatch")
        client_params = getattr(self._session, "client_params", None)
        capabilities = getattr(client_params, "capabilities", None)
        if client_params is None or getattr(capabilities, "sampling", None) is None:
            raise ProviderConfigurationError(
                self._provider, "sampling_capability_unavailable"
            )
        client_info = getattr(client_params, "clientInfo", None)
        if getattr(client_info, "name", None) != self._authorization.mcp_client_name:
            raise ProviderConfigurationError(self._provider, "sampling_client_mismatch")
        if self._grant.session_authorization_digest != (
            self._authorization.authorization_digest
        ):
            raise ProviderConfigurationError(self._provider, "sampling_grant_mismatch")
        if (
            self._grant.provider != self._provider
            or self._grant.channel != self._channel
        ):
            raise ProviderConfigurationError(self._provider, "sampling_grant_mismatch")
        if (
            self._runtime.revoked
            or self._runtime.authorization_digest
            != self._authorization.authorization_digest
            or self._runtime.mcp_session_id != self._authorization.mcp_session_id
            or self._runtime.binding_id != self._authorization.binding_id
        ):
            raise ProviderConfigurationError(
                self._provider, "sampling_session_disconnected"
            )

    def _sdk_request(self, request: ClientSamplingRequest) -> dict[str, Any]:
        requested_models = tuple(hint.name for hint in request.model_preferences.hints)
        granted_model = self._grant.models.for_route(self._grant.route)
        if not requested_models or any(
            model != granted_model for model in requested_models
        ):
            raise ProviderConfigurationError(
                self._provider, "sampling_model_not_granted"
            )
        return {
            "messages": [
                mcp_types.SamplingMessage(
                    role=message.role,
                    content=mcp_types.TextContent(
                        type="text",
                        text=message.content.text,
                    ),
                )
                for message in request.messages
            ],
            "max_tokens": request.max_tokens,
            "system_prompt": request.system_prompt,
            "include_context": "none",
            "temperature": request.temperature,
            "model_preferences": mcp_types.ModelPreferences(
                hints=[mcp_types.ModelHint(name=model) for model in requested_models],
                intelligencePriority=request.model_preferences.intelligence_priority,
                speedPriority=request.model_preferences.speed_priority,
                costPriority=request.model_preferences.cost_priority,
            ),
            "tools": None,
            "related_request_id": self._related_request_id,
        }

    def _translate_result(self, result: Any) -> ClientSamplingResult:
        if not isinstance(result, mcp_types.CreateMessageResult):
            raise ProviderProtocolError(self._provider, "invalid_sampling_result")
        try:
            result = mcp_types.CreateMessageResult.model_validate_json(
                result.model_dump_json(warnings="error")
            )
        except Exception:
            raise ProviderProtocolError(
                self._provider, "invalid_sampling_result"
            ) from None
        if result.role != "assistant" or not isinstance(
            result.content, mcp_types.TextContent
        ):
            raise ProviderProtocolError(self._provider, "invalid_sampling_result")
        text = result.content.text
        if not text or len(text) > MAX_RESPONSE_CHARS:
            raise ProviderProtocolError(self._provider, "invalid_sampling_result")
        if result.model != self._grant.models.for_route(self._grant.route):
            raise ProviderProtocolError(self._provider, "provider_model_mismatch")
        stop_reason = result.stopReason or "endTurn"
        if stop_reason not in {
            "endTurn",
            "stopSequence",
            "maxTokens",
            "contentFilter",
        }:
            raise ProviderProtocolError(self._provider, "invalid_sampling_result")
        return ClientSamplingResult(
            role="assistant",
            content=SamplingTextContent(type="text", text=text),
            model=result.model,
            stop_reason=stop_reason,
        )

    async def _sample(self, request: ClientSamplingRequest) -> ClientSamplingResult:
        if not self._entered or self._revoked:
            raise ProviderConfigurationError(self._provider, "sampling_lease_inactive")
        task = asyncio.current_task()
        if task is None:
            raise ProviderConfigurationError(
                self._provider, "sampling_context_unavailable"
            )
        self._active_tasks.add(task)
        effect_claimed = False
        try:
            try:
                self._runtime.register(task)
            except RuntimeError:
                raise ProviderConfigurationError(
                    self._provider, "sampling_session_disconnected"
                ) from None
            remaining = self._remaining_seconds()
            try:
                async with asyncio.timeout(remaining):
                    async with self._runtime.sampling_slot():
                        # Waiting for a lane consumes the same absolute lease.
                        self._remaining_seconds()
                        self._validate_live_binding()
                        sdk_request = self._sdk_request(request)
                        self._remaining_seconds()
                        try:
                            claimed = self._runtime.claim_effect(
                                self._grant.effect_digest
                            )
                        except RuntimeError:
                            raise ProviderConfigurationError(
                                self._provider, "sampling_session_disconnected"
                            ) from None
                        if not claimed:
                            raise ProviderAuthorizationInvariantError(
                                self._provider, "sampling_effect_already_claimed"
                            )
                        effect_claimed = True
                        # No suspension is permitted between the terminal
                        # effect claim and invocation of the physical MCP
                        # sampling operation.
                        raw_result = await self._session.create_message(**sdk_request)
                        self._remaining_seconds()
                        self._validate_live_binding()
                        if self._revoked:
                            raise ProviderConfigurationError(
                                self._provider, "sampling_lease_inactive"
                            )
                        return self._translate_result(raw_result)
            except TimeoutError:
                raise ProviderTransportError(self._provider, "ttl_expired") from None
        except asyncio.CancelledError:
            if effect_claimed:
                raise ProviderAuthorizationInvariantError(
                    self._provider, "sampling_effect_indeterminate"
                ) from None
            raise
        except ProviderError as exc:
            if effect_claimed and not isinstance(
                exc, ProviderAuthorizationInvariantError
            ):
                raise ProviderAuthorizationInvariantError(
                    self._provider, "sampling_effect_indeterminate"
                ) from None
            raise
        except Exception:
            if effect_claimed:
                raise ProviderAuthorizationInvariantError(
                    self._provider, "sampling_effect_indeterminate"
                ) from None
            raise ProviderTransportError(
                self._provider, "sampling_session_disconnected"
            ) from None
        finally:
            self._active_tasks.discard(task)
            self._runtime.unregister(task)


def create_stateful_mcp_sampling_lease(
    ctx: Any,
    *,
    provider: ProviderId,
    channel: ProviderChannel,
    provider_request: ProviderRequest,
    drain_timeout_seconds: Annotated[float, Field(gt=0.0, le=10.0)] = 1.0,
    clock: Clock | None = None,
) -> StatefulMCPSamplingLease:
    """Validate the current FastMCP request and create an inert sampling lease.

    The returned object must be used as an async context manager.  Merely
    constructing it has no provider, process, network, storage, or routing
    effect.
    """

    if _SAMPLING_CHANNEL_PROVIDERS.get(channel) != provider:
        raise ProviderConfigurationError(provider, "sampling_channel_mismatch")
    try:
        request_snapshot = _snapshot(ProviderRequest, provider_request)
        request_digest = provider_request_digest(request_snapshot)
    except (TypeError, ValueError):
        raise ProviderConfigurationError(
            provider, "sampling_provider_request_invalid"
        ) from None
    deadline = request_snapshot.supervision.ttl_expires_at
    if (
        not isinstance(drain_timeout_seconds, (int, float))
        or isinstance(drain_timeout_seconds, bool)
        or not math.isfinite(float(drain_timeout_seconds))
        or drain_timeout_seconds <= 0
        or drain_timeout_seconds > 10
    ):
        raise ProviderConfigurationError(provider, "sampling_drain_timeout_invalid")
    try:
        if getattr(getattr(ctx, "fastmcp"), "settings").stateless_http is not False:
            raise ProviderConfigurationError(provider, "stateful_sampling_required")
        request_context = ctx.request_context
        request = request_context.request
        session = request_context.session
        related_request_id = request_context.request_id
        if ctx.session is not session:
            raise ProviderConfigurationError(provider, "sampling_session_mismatch")
    except ProviderConfigurationError:
        raise
    except Exception:
        raise ProviderConfigurationError(
            provider, "sampling_context_unavailable"
        ) from None
    if not isinstance(request, Request) or request.scope.get("type") != "http":
        raise ProviderConfigurationError(provider, "sampling_context_unavailable")
    if related_request_id is None or not isinstance(related_request_id, (str, int)):
        raise ProviderConfigurationError(provider, "sampling_context_unavailable")

    raw_authorization = request.scope.get(MCP_SESSION_AUTHORIZATION_SCOPE_KEY)
    try:
        authorization = _snapshot(MCPSessionAuthorization, raw_authorization)
    except (TypeError, ValueError):
        raise ProviderConfigurationError(
            provider, "sampling_authorization_missing"
        ) from None
    raw_grants = request.scope.get(MCP_PROVIDER_GRANTS_SCOPE_KEY)
    if not isinstance(raw_grants, tuple):
        raise ProviderConfigurationError(provider, "sampling_grant_missing")
    provider_raw = tuple(
        item
        for item in raw_grants
        if isinstance(item, TrustedMCPProviderGrant)
        and getattr(item, "provider", None) == provider
        and getattr(item, "channel", None) == channel
    )
    if not provider_raw:
        raise ProviderConfigurationError(provider, "sampling_grant_missing")
    matching_raw = tuple(
        item
        for item in provider_raw
        if getattr(item, "provider_request_id", None) == request_snapshot.request_id
        and getattr(item, "provider_request_digest", None) == request_digest
        and getattr(item, "mcp_related_request_id", None) == related_request_id
    )
    if len(matching_raw) != 1:
        raise ProviderConfigurationError(provider, "sampling_grant_mismatch")
    raw_grant = matching_raw[0]
    try:
        grant = _snapshot(TrustedMCPProviderGrant, raw_grant)
    except (TypeError, ValueError):
        raise ProviderConfigurationError(provider, "sampling_grant_missing") from None
    if (
        grant.session_authorization_digest != authorization.authorization_digest
        or grant.issued_at < authorization.issued_at
        or grant.expires_at > authorization.expires_at
        or grant.mcp_related_request_id != related_request_id
        or grant.supervision != request_snapshot.supervision
        or grant.provider_request_id != request_snapshot.request_id
        or grant.provider_request_digest != request_digest
        or grant.route != request_snapshot.route
        or (
            request_snapshot.model is not None
            and request_snapshot.model != grant.models.for_route(grant.route)
        )
    ):
        raise ProviderConfigurationError(provider, "sampling_grant_mismatch")
    runtime = request.scope.get(MCP_SESSION_RUNTIME_SCOPE_KEY)
    if not isinstance(runtime, MCPSamplingSessionRuntime):
        raise ProviderConfigurationError(provider, "sampling_session_runtime_missing")

    return StatefulMCPSamplingLease(
        ctx=ctx,
        request=request,
        session=session,
        related_request_id=related_request_id,
        authorization=authorization,
        raw_authorization=raw_authorization,
        grant=grant,
        raw_grant=raw_grant,
        runtime=runtime,
        provider=provider,
        channel=channel,
        deadline=deadline.astimezone(UTC),
        drain_timeout_seconds=float(drain_timeout_seconds),
        clock=clock or (lambda: datetime.now(UTC)),
    )


__all__ = [
    "MAX_MCP_SAMPLING_EFFECT_CLAIMS",
    "MCP_PROVIDER_GRANTS_SCOPE_KEY",
    "MCP_SESSION_AUTHORIZATION_SCOPE_KEY",
    "MCP_SESSION_RUNTIME_SCOPE_KEY",
    "MCPSessionAuthorization",
    "MCPSamplingSessionRuntime",
    "StatefulMCPSamplingLease",
    "TrustedMCPProviderGrant",
    "create_stateful_mcp_sampling_lease",
]
