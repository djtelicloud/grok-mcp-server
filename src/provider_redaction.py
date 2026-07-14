"""Ephemeral redaction context for provider-attempt result handling.

Snapshots never leave process memory, are never serialized, and deliberately
hide their values from ``repr``.  They exist only to bridge the narrow interval
between a provider effect returning and its source revoking request authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

from .credentials import SERVER_OWNED_SECRET_ENV_NAMES


@dataclass(frozen=True, slots=True, repr=False)
class ProviderRedactionSnapshot:
    """Exact server-owned values that must be removed from one result."""

    _secret_values: tuple[str, ...]

    def secret_values(self) -> tuple[str, ...]:
        """Return the process-local values to the trusted store redactor."""

        return self._secret_values


def capture_provider_redaction_snapshot() -> ProviderRedactionSnapshot:
    """Capture current server-owned secret values without logging or I/O."""

    values = {
        str(os.environ.get(name) or "")
        for name in SERVER_OWNED_SECRET_ENV_NAMES
    }
    return ProviderRedactionSnapshot(
        tuple(sorted(value for value in values if len(value) >= 8))
    )
