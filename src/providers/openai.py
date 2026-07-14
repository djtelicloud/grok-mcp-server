"""OpenAI Responses API adapter with a fixed first-party endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
)
from .errors import ProviderProtocolError


OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
OPENAI_HOST = "api.openai.com"
OPENAI_KEY_NAMES = ("OPENAI_API_KEY",)


class OpenAIAdapter(HTTPProviderAdapter):
    provider = ProviderId.OPENAI
    channel = ProviderChannel.OPENAI_API

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
            display_name="OpenAI",
            endpoint_host=OPENAI_HOST,
            endpoint_kind="first_party_api",
            credential_kind="api_key",
            credential_env_names=OPENAI_KEY_NAMES,
            credential_state=self._credential_state(OPENAI_KEY_NAMES),
            models=load_model_pins(self.channel, self._environ),
            max_output_tokens=16_384,
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
        key = self._api_key(OPENAI_KEY_NAMES)
        _, timeout = self._limits(request)
        payload: dict[str, Any] = {
            "model": model,
            "input": [
                message.model_dump(mode="json")
                for message in self._model_messages(request)
            ],
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        data, duration_ms = await self._post_json(
            url=OPENAI_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=timeout,
        )
        text_parts: list[str] = []
        refusal_seen = False
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                    continue
                for part in item["content"]:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                    elif part.get("type") == "refusal":
                        refusal_seen = True
                        if isinstance(part.get("refusal"), str):
                            text_parts.append(part["refusal"])
        text = "".join(text_parts)
        status = str(data.get("status") or "").lower()
        incomplete = data.get("incomplete_details")
        incomplete_reason = (
            str(incomplete.get("reason") or "").lower()
            if isinstance(incomplete, dict)
            else ""
        )
        if refusal_seen:
            finish = "content_filter"
        elif status == "incomplete" and incomplete_reason == "max_output_tokens":
            finish = "length"
        elif status == "completed" and text:
            finish = "stop"
        else:
            finish = "unknown"
        if not text and finish != "content_filter":
            raise ProviderProtocolError(self.provider, "missing_text")
        usage_data = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = self._usage(
            usage_data.get("input_tokens"),
            usage_data.get("output_tokens"),
            usage_data.get("total_tokens"),
        )
        resolved_model, model_source = self._resolved_model(data.get("model"), model)
        response_id = self._response_id(data.get("id"))
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
