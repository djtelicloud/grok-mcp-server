"""Provider model pins and bounded non-secret configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import ValidationError

from .contracts import (
    ProviderChannel,
    ProviderId,
    ProviderModelPins,
    RouteClass,
    is_safe_model_id,
)
from .errors import ProviderConfigurationError


DEFAULT_MODEL_PINS: dict[ProviderChannel, ProviderModelPins] = {
    ProviderChannel.OPENAI_API: ProviderModelPins(
        planning="gpt-5.1",
        coding="gpt-5.1",
        vision="gpt-5.1",
        research="gpt-5.1",
    ),
    ProviderChannel.ANTHROPIC_API: ProviderModelPins(
        planning="claude-fable-5",
        coding="claude-sonnet-5",
        vision="claude-sonnet-5",
        research="claude-fable-5",
    ),
    ProviderChannel.GEMINI_API_KEY: ProviderModelPins(
        planning="gemini-3.5-flash",
        coding="gemini-3.5-flash",
        vision="gemini-3.5-flash",
        research="gemini-3.5-flash",
    ),
    ProviderChannel.VERTEX_ADC: ProviderModelPins(
        planning="gemini-3.5-flash",
        coding="gemini-3.5-flash",
        vision="gemini-3.5-flash",
        research="gemini-3.5-flash",
    ),
}

_PREFIXES = {
    ProviderChannel.OPENAI_API: "OPENAI",
    ProviderChannel.ANTHROPIC_API: "ANTHROPIC",
    ProviderChannel.GEMINI_API_KEY: "GEMINI",
    ProviderChannel.VERTEX_ADC: "VERTEX",
}

_PROVIDERS = {
    ProviderChannel.OPENAI_API: ProviderId.OPENAI,
    ProviderChannel.ANTHROPIC_API: ProviderId.ANTHROPIC,
    ProviderChannel.GEMINI_API_KEY: ProviderId.GOOGLE,
    ProviderChannel.VERTEX_ADC: ProviderId.GOOGLE,
}

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_LOCATION_RE = re.compile(r"^(?:global|us|eu|[a-z]+-[a-z]+[0-9])$")


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    value = str(environ.get(name) or "").strip()
    return value or None


def load_model_pins(
    channel: ProviderChannel,
    environ: Mapping[str, str],
) -> ProviderModelPins:
    """Resolve route pin > provider pin > stable first-party default.

    Invalid values fail closed while naming only the environment variable, never
    its content.
    """

    provider = _PROVIDERS[channel]
    prefix = _PREFIXES[channel]
    base_name = f"UNIGROK_{prefix}_MODEL"
    base = _env_value(environ, base_name)
    defaults = DEFAULT_MODEL_PINS[channel]
    values: dict[str, str] = {}
    for route in RouteClass:
        route_name = f"UNIGROK_{prefix}_{route.value.upper()}_MODEL"
        value = _env_value(environ, route_name) or base or defaults.for_route(route)
        if not is_safe_model_id(value):
            failing_name = route_name if _env_value(environ, route_name) else base_name
            raise ProviderConfigurationError(provider, f"invalid_model_pin:{failing_name}")
        values[route.value] = value
    try:
        return ProviderModelPins(**values)
    except ValidationError:  # defensive: the values above are prechecked
        raise ProviderConfigurationError(provider, "invalid_model_pins") from None


def vertex_location(environ: Mapping[str, str]) -> str:
    location = _env_value(environ, "UNIGROK_VERTEX_LOCATION") or "global"
    if not _LOCATION_RE.fullmatch(location):
        raise ProviderConfigurationError(ProviderId.GOOGLE, "invalid_vertex_location")
    return location


def configured_vertex_project(environ: Mapping[str, str]) -> str | None:
    project = _env_value(environ, "UNIGROK_VERTEX_PROJECT") or _env_value(
        environ, "GOOGLE_CLOUD_PROJECT"
    )
    if project is not None and not _PROJECT_RE.fullmatch(project):
        raise ProviderConfigurationError(ProviderId.GOOGLE, "invalid_vertex_project")
    return project


def validate_vertex_project(project: str) -> str:
    if not _PROJECT_RE.fullmatch(str(project or "")):
        raise ProviderConfigurationError(ProviderId.GOOGLE, "invalid_vertex_project")
    return project
