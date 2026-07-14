"""Provider-neutral semantic inference adapters.

This package is intentionally not wired into ``src.utils`` or the public agent
yet.  It supplies strict contracts and first-party HTTP transports for the
future policy broker.
"""

from .anthropic import AnthropicAdapter
from .contracts import (
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
    ProviderModelPins,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    RouteClass,
    WorkerAuthority,
    model_visible_messages,
)
from .errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderProtocolError,
    ProviderTransportError,
)
from .gemini import GeminiAdapter
from .openai import OpenAIAdapter
from .registry import build_provider_registry
from .vertex import ADCIdentity, VertexADCAdapter, load_google_adc_identity

__all__ = [
    "ADCIdentity",
    "AnthropicAdapter",
    "CredentialPlane",
    "CredentialState",
    "GeminiAdapter",
    "GrokSupervisorBinding",
    "OpenAIAdapter",
    "ProviderAdapter",
    "ProviderAttemptResult",
    "ProviderAttemptStart",
    "ProviderChannel",
    "ProviderConfigurationError",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderFailureReceipt",
    "ProviderId",
    "ProviderMessage",
    "ProviderModelPins",
    "ProviderProtocolError",
    "ProviderReceipt",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderTokenUsage",
    "ProviderTransportError",
    "RouteClass",
    "VertexADCAdapter",
    "build_provider_registry",
    "load_google_adc_identity",
    "model_visible_messages",
    "WorkerAuthority",
]
