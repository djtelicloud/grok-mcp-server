"""Shared bounded HTTP transport for provider adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from .contracts import (
    CredentialState,
    ProviderChannel,
    ProviderAttemptResult,
    ProviderDescriptor,
    ProviderFailureReceipt,
    ProviderId,
    ProviderMessage,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    is_safe_model_id,
    is_safe_response_id,
    model_visible_messages,
)
from .errors import (
    ProviderError,
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderTransportError,
)


MAX_PROVIDER_RESPONSE_BYTES = 4 * 1024 * 1024
Clock = Callable[[], datetime]
FailureKind = Literal["configuration", "transport", "protocol", "internal"]


class HTTPProviderAdapter:
    """Base for one-shot first-party JSON APIs.

    An injected AsyncClient makes every wire interaction deterministic in tests.
    Production clients are short-lived, do not inherit proxy environment state,
    and never follow redirects carrying credentials.
    """

    provider: ProviderId
    channel: ProviderChannel

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._client = client
        self._environ = environ if environ is not None else os.environ
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def descriptor(self) -> ProviderDescriptor:
        raise NotImplementedError

    def _credential_state(self, names: tuple[str, ...]) -> CredentialState:
        return (
            CredentialState.CONFIGURED
            if any(str(self._environ.get(name) or "").strip() for name in names)
            else CredentialState.MISSING
        )

    def _api_key(self, names: tuple[str, ...]) -> SecretStr:
        for name in names:
            value = str(self._environ.get(name) or "").strip()
            if value:
                return SecretStr(value)
        raise ProviderConfigurationError(self.provider, "credential_missing")

    def _model(self, request: ProviderRequest) -> str:
        if request.route not in self.descriptor.supported_routes:
            raise ProviderConfigurationError(self.provider, "unsupported_route")
        model = request.model or self.descriptor.models.for_route(request.route)
        if not is_safe_model_id(model):
            raise ProviderConfigurationError(self.provider, "invalid_model")
        return model

    def _remaining_ttl(self, request: ProviderRequest) -> float:
        now = self._clock()
        if now.tzinfo is None:
            raise ProviderConfigurationError(self.provider, "clock_not_timezone_aware")
        remaining_ttl = (request.supervision.ttl_expires_at - now).total_seconds()
        if remaining_ttl <= 0:
            raise ProviderConfigurationError(self.provider, "ttl_expired")
        return remaining_ttl

    def _limits(self, request: ProviderRequest) -> tuple[int, float]:
        remaining_ttl = self._remaining_ttl(request)
        return (
            min(request.max_output_tokens, self.descriptor.max_output_tokens),
            min(
                request.timeout_seconds,
                self.descriptor.max_timeout_seconds,
                remaining_ttl,
            ),
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Run the complete worker call under one absolute supervisor deadline."""

        remaining_ttl = self._remaining_ttl(request)
        try:
            async with asyncio.timeout(remaining_ttl):
                response = await self._complete(request)
        except TimeoutError:
            raise ProviderTransportError(self.provider, "ttl_expired") from None
        # The worker may finish at the deadline boundary. Never emit a late result.
        self._remaining_ttl(request)
        return response

    async def _complete(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError

    async def _run_with_ttl(
        self,
        request: ProviderRequest,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Bound a subordinate acquisition to the currently remaining lease."""

        try:
            async with asyncio.timeout(self._remaining_ttl(request)):
                return await operation()
        except TimeoutError:
            raise ProviderTransportError(self.provider, "ttl_expired") from None

    def _model_messages(self, request: ProviderRequest) -> list[ProviderMessage]:
        return list(model_visible_messages(request))

    async def attempt(self, request: ProviderRequest) -> ProviderAttemptResult:
        """Return a complete worker result without granting it semantic authority."""

        started = time.monotonic()
        requested_model = request.model or self.descriptor.models.for_route(request.route)
        try:
            response = await self.complete(request)
        except ProviderError as exc:
            return ProviderAttemptResult(
                status="failed",
                failure=self._failure_receipt(
                    request=request,
                    requested_model=requested_model,
                    error_kind=self._error_kind(exc),
                    error_code=exc.code,
                    duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                ),
            )
        except Exception:
            return ProviderAttemptResult(
                status="failed",
                failure=self._failure_receipt(
                    request=request,
                    requested_model=requested_model,
                    error_kind="internal",
                    error_code="unexpected_error",
                    duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                ),
            )
        return ProviderAttemptResult(status="returned", response=response)

    def _error_kind(self, error: ProviderError) -> FailureKind:
        if isinstance(error, ProviderConfigurationError):
            return "configuration"
        if isinstance(error, ProviderTransportError):
            return "transport"
        if isinstance(error, ProviderProtocolError):
            return "protocol"
        return "internal"

    def _failure_receipt(
        self,
        *,
        request: ProviderRequest,
        requested_model: str,
        error_kind: FailureKind,
        error_code: str,
        duration_ms: int,
    ) -> ProviderFailureReceipt:
        descriptor = self.descriptor
        return ProviderFailureReceipt(
            request_id=request.request_id,
            supervision=request.supervision,
            provider=self.provider,
            channel=self.channel,
            credential_plane=descriptor.credential_plane,
            route=request.route,
            requested_model=requested_model,
            endpoint_host=descriptor.endpoint_host,
            endpoint_kind=descriptor.endpoint_kind,
            credential_kind=descriptor.credential_kind,
            billing_class=descriptor.billing_class,
            client_identity=descriptor.client_identity,
            error_kind=error_kind,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    async def _post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], int]:
        started = time.monotonic()
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout_seconds),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    body, status_code = await self._stream_json_body(
                        client=client,
                        url=url,
                        headers=headers,
                        payload=payload,
                        timeout_seconds=timeout_seconds,
                    )
            else:
                body, status_code = await self._stream_json_body(
                    client=self._client,
                    url=url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
        except httpx.TimeoutException:
            raise ProviderTransportError(self.provider, "timeout") from None
        except httpx.HTTPError:
            raise ProviderTransportError(self.provider, "transport_error") from None
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        if status_code < 200 or status_code >= 300:
            raise ProviderTransportError(self.provider, f"http_{status_code}")
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeError, ValueError):
            raise ProviderProtocolError(self.provider, "invalid_json") from None
        if not isinstance(value, dict):
            raise ProviderProtocolError(self.provider, "invalid_response_shape")
        return value, duration_ms

    async def _stream_json_body(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> tuple[bytes, int]:
        body = bytearray()
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        ) as response:
            status_code = response.status_code
            if status_code < 200 or status_code >= 300:
                return b"", status_code
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise ProviderProtocolError(self.provider, "response_too_large")
                body.extend(chunk)
        return bytes(body), status_code

    def _usage(
        self,
        input_tokens: Any,
        output_tokens: Any,
        total_tokens: Any = None,
    ) -> ProviderTokenUsage:
        def bounded_int(value: Any) -> int | None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return min(value, 10**12)

        input_count = bounded_int(input_tokens)
        output_count = bounded_int(output_tokens)
        total_count = bounded_int(total_tokens)
        raw_values = (input_tokens, output_tokens, total_tokens)
        parsed_values = (input_count, output_count, total_count)
        invalid_reported = any(
            raw is not None and parsed is None
            for raw, parsed in zip(raw_values, parsed_values, strict=True)
        )
        if input_count is not None and output_count is not None and not invalid_reported:
            components_total = input_count + output_count
            if total_count is None:
                total_count = components_total
                source = "derived"
            elif total_count >= components_total:
                source = "provider_exact"
            else:
                total_count = None
                source = "partial"
        elif any(raw is not None for raw in raw_values):
            source = "partial"
        else:
            source = "unavailable"
        return ProviderTokenUsage(
            input_tokens=input_count,
            output_tokens=output_count,
            total_tokens=total_count,
            source=source,
        )

    def _resolved_model(
        self, value: Any, requested: str
    ) -> tuple[str, Literal["provider_reported", "requested_fallback"]]:
        candidate = str(value or "").strip()
        if is_safe_model_id(candidate):
            return candidate, "provider_reported"
        return requested, "requested_fallback"

    def _response_id(self, value: Any) -> str | None:
        candidate = str(value or "").strip()
        return candidate if is_safe_response_id(candidate) else None

    def _receipt(
        self,
        *,
        request: ProviderRequest,
        requested_model: str,
        resolved_model: str,
        model_source: Literal["provider_reported", "requested_fallback"],
        response_id: str | None,
        duration_ms: int,
        usage: ProviderTokenUsage,
        region: str,
        account_fingerprint: str | None = None,
    ) -> ProviderReceipt:
        descriptor = self.descriptor
        return ProviderReceipt(
            request_id=request.request_id,
            supervision=request.supervision,
            provider=self.provider,
            channel=self.channel,
            credential_plane=descriptor.credential_plane,
            route=request.route,
            requested_model=requested_model,
            resolved_model=resolved_model,
            model_source=model_source,
            endpoint_host=descriptor.endpoint_host,
            endpoint_kind=descriptor.endpoint_kind,
            credential_kind=descriptor.credential_kind,
            billing_class=descriptor.billing_class,
            client_identity=descriptor.client_identity,
            region=region,
            account_fingerprint=account_fingerprint,
            response_id=response_id,
            duration_ms=duration_ms,
            usage=usage,
        )


def opaque_fingerprint(value: str) -> str:
    """Identify a non-secret account/project without exposing its raw value."""

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
