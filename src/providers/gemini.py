"""Google Gemini API-key adapter using the fixed generateContent endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

import httpx

from .base import Clock, HTTPProviderAdapter
from .config import load_model_pins
from .contracts import (
    CredentialPlane,
    ProviderChannel,
    ProviderDescriptor,
    ProviderId,
    ProviderRequest,
    ProviderResponse,
    transport_resource_identity,
)
from .google_common import (
    build_generate_content_payload,
    parse_generate_content_response,
)


GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_KEY_NAMES = ("GEMINI_API_KEY",)


class GeminiAdapter(HTTPProviderAdapter):
    provider = ProviderId.GOOGLE
    channel = ProviderChannel.GEMINI_API_KEY

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(client=client, environ=environ, clock=clock)
        self._descriptor = ProviderDescriptor(
            provider=self.provider,
            channel=self.channel,
            credential_plane=CredentialPlane.METERED_API,
            display_name="Google Gemini API",
            endpoint_host=GEMINI_HOST,
            endpoint_kind="first_party_api",
            credential_kind="api_key",
            transport_resource_identity=transport_resource_identity(
                "gemini_api_endpoint",
                GEMINI_HOST,
            ),
            credential_env_names=GEMINI_KEY_NAMES,
            credential_state=self._credential_state(GEMINI_KEY_NAMES),
            models=load_model_pins(self.channel, self._environ),
            max_output_tokens=32_768,
            max_timeout_seconds=120.0,
            data_handling="provider_managed",
            residency="provider_default",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def _complete(self, request: ProviderRequest) -> ProviderResponse:
        model = self._model(request)
        max_tokens, _ = self._limits(request)
        key = self._api_key(GEMINI_KEY_NAMES)
        _, timeout = self._limits(request)
        endpoint = (
            f"https://{GEMINI_HOST}/v1beta/models/"
            f"{quote(model, safe='._-')}:generateContent"
        )
        data, duration_ms = await self._post_json(
            url=endpoint,
            headers={
                "x-goog-api-key": key.get_secret_value(),
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
            region="provider_default",
        )
        return ProviderResponse(
            provider=self.provider,
            channel=self.channel,
            model=resolved_model,
            text=text,
            finish_reason=finish,
            receipt=receipt,
        )
