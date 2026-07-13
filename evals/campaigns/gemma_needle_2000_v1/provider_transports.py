"""Credential-reference transports for the campaign provider smoke gate.

The transports intentionally never accept raw credential values. Vertex uses
Google Application Default Credentials in place, while Grok is reached through
the loopback UniGrok MCP so its API key and CLI OAuth session remain server-side.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


PROVIDER_PROBE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "probe": {"type": "string", "enum": ["provider-contract-v1"]},
        "nonce": {
            "type": "string",
            "minLength": 8,
            "maxLength": 96,
            "pattern": "^[A-Za-z0-9_.-]+$",
        },
    },
    "required": ["probe", "nonce"],
}


def _integer_setting(
    settings: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = settings.get(name, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{name} must be an integer.")
    if not minimum <= raw <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return raw


def _temperature(settings: dict[str, Any]) -> float:
    raw = settings.get("temperature", 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("temperature must be numeric.")
    value = float(raw)
    if not 0 <= value <= 1:
        raise ValueError("temperature must be between 0 and 1.")
    return value


@dataclass(frozen=True)
class VertexADCTransport:
    """Call Gemini on Vertex through standard host ADC discovery."""

    project: str
    location: str
    model: str
    total_attempt_limit: int = field(default=1, init=False)
    thinking_budget: int = field(default=0, init=False)

    def build_http_options(self, types_module):
        """Build the SDK options separately so the one-attempt wire is testable."""

        return types_module.HttpOptions(
            api_version="v1",
            retry_options=types_module.HttpRetryOptions(
                attempts=self.total_attempt_limit
            ),
        )

    def __call__(self, request: str, settings: dict[str, Any]) -> dict[str, Any]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "Vertex transport requires the optional campaign dependency."
            ) from exc

        max_output_tokens = _integer_setting(
            settings,
            "max_output_tokens",
            default=64,
            minimum=16,
            maximum=512,
        )
        config = types.GenerateContentConfig(
            temperature=_temperature(settings),
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=PROVIDER_PROBE_JSON_SCHEMA,
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=self.thinking_budget,
            ),
        )
        client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
            http_options=self.build_http_options(types),
        )
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=request,
                config=config,
            )
        finally:
            client.close()

        content = getattr(response, "text", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Vertex returned no text artifact.")

        usage = getattr(response, "usage_metadata", None)
        receipt: dict[str, Any] = {
            "auth_kind": "google_adc",
            "configured_model": self.model,
            "location": self.location,
            "project": self.project,
            "provider": "vertex",
            "resolved_model": str(
                getattr(response, "model_version", None) or self.model
            ),
            "resolved_plane": "api",
            "thinking_budget": self.thinking_budget,
            "total_attempt_limit": self.total_attempt_limit,
        }
        if usage is not None:
            for source, target in (
                ("prompt_token_count", "input_tokens"),
                ("candidates_token_count", "output_tokens"),
                ("total_token_count", "total_tokens"),
            ):
                value = getattr(usage, source, None)
                if isinstance(value, int):
                    receipt[target] = value

        return {"content": content, "transport_receipt": receipt}


@dataclass(frozen=True)
class UniGrokMCPTransport:
    """Call a pinned Grok model without transferring its credentials."""

    endpoint: str
    model: str
    plane: str = "cli"
    fallback_policy: str = "same_plane"
    client_id: str = "antigravity-campaign-gemma-needle-2000-v1"
    timeout_seconds: int = 180

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 4765
            or parsed.path.rstrip("/") != "/mcp"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("UniGrok transport requires the canonical loopback MCP endpoint.")

    def agent_arguments(self, request: str) -> dict[str, Any]:
        """Build the public UniGrok MCP contract, not the internal tool signature."""

        return {
            "prompt": request,
            "mode": "fast",
            "model": self.model,
            "plane": self.plane,
            "fallback_policy": self.fallback_policy,
        }

    def http_client_options(self, headers: dict[str, str]) -> dict[str, Any]:
        return {
            "headers": headers,
            "timeout": float(self.timeout_seconds),
            "trust_env": False,
        }

    def __call__(self, request: str, settings: dict[str, Any]) -> dict[str, Any]:
        # The public UniGrok agent contract does not expose a hard output-token
        # cap. Stage 0.5 therefore bounds calls and accepted schema size, and its
        # receipts state this limitation instead of pretending the Vertex cap
        # also applies to Grok.
        if settings != {"accepted_artifact": "provider-contract-v1", "mode": "fast"}:
            raise ValueError("UniGrok smoke settings do not match the bounded contract.")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._call(request))
        raise RuntimeError("UniGrok sync transport cannot run inside an event loop.")

    async def _call(self, request: str) -> dict[str, Any]:
        headers = {"X-Client-ID": self.client_id}
        gateway_token = os.environ.get("UNIGROK_CLIENT_TOKEN", "").strip()
        if gateway_token:
            headers["Authorization"] = f"Bearer {gateway_token}"

        timeout = float(self.timeout_seconds)
        async with httpx.AsyncClient(**self.http_client_options(headers)) as client:
            async with streamable_http_client(
                self.endpoint,
                http_client=client,
            ) as (read, write, _):
                async with ClientSession(
                    read,
                    write,
                    read_timeout_seconds=timedelta(seconds=timeout),
                ) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "agent",
                        self.agent_arguments(request),
                    )

        if result.isError:
            raise RuntimeError("UniGrok agent tool reported an error.")
        payload = result.structuredContent
        if not isinstance(payload, dict):
            raise RuntimeError("UniGrok returned no structured agent result.")

        content = payload.get("response")
        resolved_model = str(payload.get("model") or "")
        resolved_plane = str(
            payload.get("resolved_plane") or payload.get("plane") or ""
        ).upper()
        finish_reason = str(payload.get("finish_reason") or "unknown")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("UniGrok returned no text artifact.")
        if resolved_model != self.model:
            raise RuntimeError("UniGrok did not execute the pinned model.")
        if resolved_plane != self.plane.upper():
            raise RuntimeError("UniGrok crossed the requested credential plane.")
        if payload.get("degraded") is True:
            raise RuntimeError("UniGrok reported degraded execution.")
        if finish_reason != "final_answer":
            raise RuntimeError("UniGrok did not report a final answer.")

        receipt = {
            "auth_kind": "server_managed",
            "billing_class": payload.get("billing_class"),
            "configured_model": self.model,
            "fallback_policy": self.fallback_policy,
            "finish_reason": finish_reason,
            "provider": "unigrok",
            "provider_output_token_limit": "not_exposed_by_public_agent",
            "resolved_model": resolved_model,
            "resolved_plane": resolved_plane,
            "route": payload.get("route"),
        }
        cost = payload.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            receipt["cost_usd"] = float(cost)
        return {"content": content, "transport_receipt": receipt}
