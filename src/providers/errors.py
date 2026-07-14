"""Secret-safe provider error taxonomy.

Errors carry bounded machine codes only.  Provider response bodies, request
URLs, authentication material, and exception text never cross this boundary.
"""

from __future__ import annotations

from .contracts import ProviderId


class ProviderError(RuntimeError):
    def __init__(self, provider: ProviderId, code: str) -> None:
        self.provider = provider
        self.code = code
        super().__init__(f"{provider.value} provider failed ({code})")


class ProviderConfigurationError(ProviderError):
    pass


class ProviderTransportError(ProviderError):
    pass


class ProviderProtocolError(ProviderError):
    pass
