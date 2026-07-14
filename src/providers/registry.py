"""Construction seam for the provider-neutral API adapter registry."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from .anthropic import AnthropicAdapter
from .base import Clock
from .contracts import ProviderAdapter, ProviderChannel
from .gemini import GeminiAdapter
from .openai import OpenAIAdapter
from .vertex import ADCTokenProvider, VertexADCAdapter


def build_provider_registry(
    *,
    environ: Mapping[str, str] | None = None,
    clients: Mapping[ProviderChannel, httpx.AsyncClient] | None = None,
    vertex_token_provider: ADCTokenProvider | None = None,
    clock: Clock | None = None,
) -> dict[ProviderChannel, ProviderAdapter]:
    """Build adapters without performing discovery or provider calls."""

    client_map = clients or {}
    return {
        ProviderChannel.OPENAI_API: OpenAIAdapter(
            client=client_map.get(ProviderChannel.OPENAI_API),
            environ=environ,
            clock=clock,
        ),
        ProviderChannel.ANTHROPIC_API: AnthropicAdapter(
            client=client_map.get(ProviderChannel.ANTHROPIC_API),
            environ=environ,
            clock=clock,
        ),
        ProviderChannel.GEMINI_API_KEY: GeminiAdapter(
            client=client_map.get(ProviderChannel.GEMINI_API_KEY),
            environ=environ,
            clock=clock,
        ),
        ProviderChannel.VERTEX_ADC: VertexADCAdapter(
            client=client_map.get(ProviderChannel.VERTEX_ADC),
            environ=environ,
            token_provider=vertex_token_provider,
            clock=clock,
        ),
    }
