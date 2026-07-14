"""Vertex AI Gemini adapter using Google Application Default Credentials."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from .base import Clock, HTTPProviderAdapter, opaque_fingerprint
from .config import (
    configured_vertex_project,
    load_model_pins,
    validate_vertex_project,
    vertex_location,
)
from .contracts import (
    CredentialPlane,
    CredentialState,
    ProviderChannel,
    ProviderDescriptor,
    ProviderId,
    ProviderRequest,
    ProviderResponse,
    StrictContract,
)
from .errors import ProviderConfigurationError
from .google_common import build_generate_content_payload, parse_generate_content_response


GOOGLE_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
VERTEX_CREDENTIAL_NAMES = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT")


class ADCIdentity(StrictContract):
    access_token: SecretStr
    project_id: str


ADCTokenProvider = Callable[[], Awaitable[ADCIdentity]]


async def load_google_adc_identity(timeout_seconds: float = 60.0) -> ADCIdentity:
    """Resolve and refresh ADC off the event loop.

    All third-party exception details are suppressed at the adapter boundary so
    credential paths, token fragments, and account identifiers cannot escape.
    """

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ProviderConfigurationError(ProviderId.GOOGLE, "adc_timeout_invalid")
    bounded_timeout = min(timeout_seconds, 60.0)

    def load() -> ADCIdentity:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, project_id = google.auth.default(scopes=[GOOGLE_CLOUD_SCOPE])
        request = Request()

        def bounded_request(*args, timeout=None, **kwargs):
            requested_timeout = (
                float(timeout) if isinstance(timeout, int | float) else bounded_timeout
            )
            if not math.isfinite(requested_timeout) or requested_timeout <= 0:
                requested_timeout = bounded_timeout
            return request(
                *args,
                timeout=min(requested_timeout, bounded_timeout),
                **kwargs,
            )

        credentials.refresh(bounded_request)
        token = str(getattr(credentials, "token", None) or "")
        return ADCIdentity(access_token=SecretStr(token), project_id=str(project_id or ""))

    try:
        identity = await asyncio.to_thread(load)
    except Exception:
        raise ProviderConfigurationError(ProviderId.GOOGLE, "adc_unavailable") from None
    if not identity.access_token.get_secret_value():
        raise ProviderConfigurationError(ProviderId.GOOGLE, "adc_token_missing")
    return identity


class VertexADCAdapter(HTTPProviderAdapter):
    provider = ProviderId.GOOGLE
    channel = ProviderChannel.VERTEX_ADC

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        environ: Mapping[str, str] | None = None,
        token_provider: ADCTokenProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(client=client, environ=environ, clock=clock)
        self._location = vertex_location(self._environ)
        self._configured_project = configured_vertex_project(self._environ)
        self._token_provider = token_provider or load_google_adc_identity
        self._host = (
            "aiplatform.googleapis.com"
            if self._location == "global"
            else f"{self._location}-aiplatform.googleapis.com"
        )
        self._descriptor = ProviderDescriptor(
            provider=self.provider,
            channel=self.channel,
            credential_plane=CredentialPlane.METERED_API,
            display_name="Google Vertex AI",
            endpoint_host=self._host,
            endpoint_kind="vertex_ai",
            credential_kind="google_adc",
            credential_env_names=VERTEX_CREDENTIAL_NAMES,
            # ADC may come from a workload identity or metadata server without
            # any environment variable, so availability is resolved at call time.
            credential_state=CredentialState.DEFERRED,
            models=load_model_pins(self.channel, self._environ),
            max_output_tokens=32_768,
            max_timeout_seconds=120.0,
            data_handling="project_policy",
            residency=self._location,
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def _complete(self, request: ProviderRequest) -> ProviderResponse:
        model = self._model(request)
        max_tokens, _ = self._limits(request)
        try:
            if self._token_provider is load_google_adc_identity:
                identity = await self._run_with_ttl(
                    request,
                    lambda: load_google_adc_identity(
                        timeout_seconds=self._remaining_ttl(request),
                    ),
                )
            else:
                identity = await self._run_with_ttl(
                    request,
                    self._token_provider,
                )
        except ProviderConfigurationError:
            raise
        except Exception:
            raise ProviderConfigurationError(self.provider, "adc_unavailable") from None
        token = identity.access_token.get_secret_value()
        if not token:
            raise ProviderConfigurationError(self.provider, "adc_token_missing")
        project = validate_vertex_project(
            self._configured_project or identity.project_id
        )
        _, timeout = self._limits(request)
        endpoint = (
            f"https://{self._host}/v1/projects/{quote(project, safe='-')}/"
            f"locations/{quote(self._location, safe='-')}/publishers/google/models/"
            f"{quote(model, safe='._-')}:generateContent"
        )
        data, duration_ms = await self._post_json(
            url=endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            payload=build_generate_content_payload(
                request,
                max_output_tokens=max_tokens,
                messages=self._model_messages(request),
            ),
            timeout_seconds=timeout,
        )
        (
            text,
            finish,
            raw_model,
            raw_response_id,
            input_tokens,
            output_tokens,
            total_tokens,
        ) = parse_generate_content_response(self.provider, data)
        usage = self._usage(input_tokens, output_tokens, total_tokens)
        resolved_model, model_source = self._resolved_model(raw_model, model)
        response_id = self._response_id(raw_response_id)
        receipt = self._receipt(
            request=request,
            requested_model=model,
            resolved_model=resolved_model,
            model_source=model_source,
            response_id=response_id,
            duration_ms=duration_ms,
            usage=usage,
            region=self._location,
            account_fingerprint=opaque_fingerprint(project),
        )
        return ProviderResponse(
            provider=self.provider,
            channel=self.channel,
            model=resolved_model,
            text=text,
            finish_reason=finish,
            receipt=receipt,
        )
