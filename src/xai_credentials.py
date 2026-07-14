"""Secret-safe resolution for xAI's distinct management authority.

The canonical UniGrok variable and the xAI SDK alias name the same credential.
Callers may support either spelling, but conflicting values are ambiguous and
must fail closed before a management request or SDK client is created.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


def _resolve_optional_xai_management_key(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return one unambiguous management key without logging its value."""

    source = os.environ if environ is None else environ
    canonical = str(source.get("XAI_MANAGEMENT_API_KEY", "")).strip()
    sdk_alias = str(source.get("XAI_MANAGEMENT_KEY", "")).strip()
    if canonical and sdk_alias and canonical != sdk_alias:
        raise ValueError(
            "XAI_MANAGEMENT_API_KEY and XAI_MANAGEMENT_KEY are both configured "
            "with different values."
        )
    return canonical or sdk_alias or None


def _require_xai_management_key(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the management credential or fail before any remote effect."""

    resolved = _resolve_optional_xai_management_key(environ)
    if resolved is None:
        raise ValueError(
            "XAI_MANAGEMENT_API_KEY or XAI_MANAGEMENT_KEY must be configured."
        )
    return resolved
