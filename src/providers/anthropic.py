"""Anthropic Messages API adapter with server-side key aliases."""

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
from .errors import ProviderConfigurationError, ProviderProtocolError


ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_HOST = "api.anthropic.com"
# Canonical Anthropic name wins; CLAUDE_API_KEY is retained as the user's
# existing server-side alias.  Neither name or value is sent in receipts.
ANTHROPIC_KEY_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")


class AnthropicAdapter(HTTPProviderAdapter):
    provider = ProviderId.ANTHROPIC
    channel = ProviderChannel.ANTHROPIC_API

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
            display_name="Anthropic Claude",
            endpoint_host=ANTHROPIC_HOST,
            endpoint_kind="first_party_api",
            credential_kind="api_key",
            credential_env_names=ANTHROPIC_KEY_NAMES,
            credential_state=self._credential_state(ANTHROPIC_KEY_NAMES),
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
        if request.temperature is not None and model.startswith(
            ("claude-fable-5", "claude-sonnet-5")
        ):
            raise ProviderConfigurationError(self.provider, "temperature_unsupported")
        key = self._api_key(ANTHROPIC_KEY_NAMES)
        _, timeout = self._limits(request)
        model_messages = self._model_messages(request)
        system_parts = [
            message.content for message in model_messages if message.role == "system"
        ]
        messages = [
            {"role": message.role, "content": message.content}
            for message in model_messages
            if message.role != "system"
        ]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        data, duration_ms = await self._post_json(
            url=ANTHROPIC_ENDPOINT,
            headers={
                "x-api-key": key.get_secret_value(),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=timeout,
        )
        content = data.get("content")
        blocks = content if isinstance(content, list) else []
        text = "".join(
            str(block.get("text"))
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        stop_reason = str(data.get("stop_reason") or "").lower()
        if stop_reason == "max_tokens":
            finish = "length"
        elif stop_reason == "tool_use":
            finish = "tool_calls"
        elif stop_reason in {"refusal", "model_context_window_exceeded"}:
            finish = "content_filter" if stop_reason == "refusal" else "length"
        elif stop_reason in {"end_turn", "stop_sequence", "pause_turn"} and text:
            finish = "stop"
        else:
            finish = "unknown"
        if not text and finish != "content_filter":
            raise ProviderProtocolError(self.provider, "missing_text")
        usage_data = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = self._usage(
            usage_data.get("input_tokens"),
            usage_data.get("output_tokens"),
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
