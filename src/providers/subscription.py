"""Bounded subscription transports for subordinate semantic workers.

These adapters are deliberately request-scoped and inert until ``complete`` is
called by a future Grok-owned broker.  They do not discover IDEs, inspect auth
files, copy credentials, route work, or grant a worker completion authority.
"""

from __future__ import annotations
from ..utils import create_scrubbed_subprocess_exec

import asyncio
import hashlib
import json
import os
import re
import signal
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .base import Clock, HTTPProviderAdapter
from .config import DEFAULT_MODEL_PINS
from .contracts import (
    MAX_REQUEST_CHARS,
    MAX_RESPONSE_CHARS,
    CredentialPlane,
    CredentialState,
    GrokSupervisorBinding,
    ProviderAdapter,
    ProviderAttemptResult,
    ProviderChannel,
    ProviderDescriptor,
    ProviderFailureReceipt,
    ProviderId,
    ProviderModelPins,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    RouteClass,
    is_safe_model_id,
    transport_resource_identity,
)
from .errors import (
    ProviderAuthorizationInvariantError,
    ProviderConfigurationError,
    ProviderError,
    ProviderProtocolError,
    ProviderTransportError,
)


MAX_CLI_STDOUT_BYTES = 2 * 1024 * 1024
MAX_CLI_STDERR_BYTES = 64 * 1024
LOCAL_PROCESS_ENDPOINT = "local-process"
MCP_CLIENT_ENDPOINT = "mcp-client"
CLAUDE_WORKER_SYSTEM_PROMPT = (
    "You are a subordinate semantic worker for a Grok-supervised request. "
    "Do not use tools, inspect files, take effects, route, verify, harvest, or "
    "claim final authority. Return only the requested bounded observation."
)

CLAUDE_SUBSCRIPTION_MODELS = DEFAULT_MODEL_PINS[ProviderChannel.ANTHROPIC_API]

SAMPLING_CHANNEL_PROVIDERS: dict[ProviderChannel, ProviderId] = {
    ProviderChannel.OPENAI_MCP_SAMPLING: ProviderId.OPENAI,
    ProviderChannel.ANTHROPIC_MCP_SAMPLING: ProviderId.ANTHROPIC,
    ProviderChannel.GOOGLE_MCP_SAMPLING: ProviderId.GOOGLE,
}

# Codex CLI is intentionally absent: its current command surface has no proven
# no-tools execution boundary.  Antigravity is represented only by a client-
# advertised MCP sampling callback; no IDE token or auth file is read here.
# xAI management credentials are likewise absent: they are Collections/admin
# authority, never an inference transport or worker fallback.
DISABLED_SUBSCRIPTION_SURFACES = frozenset({"codex_cli", "antigravity_token"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )


class ClaudeCLIUsage(_StrictModel):
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    cache_creation_input_tokens: Annotated[int, Field(ge=0)] | None = None
    cache_read_input_tokens: Annotated[int, Field(ge=0)] | None = None
    server_tool_use: dict[str, Annotated[int, Field(ge=0)]] | None = None
    service_tier: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    cache_creation: dict[str, Annotated[int, Field(ge=0)]] | None = None
    inference_geo: Annotated[str, Field(max_length=64)] | None = None
    iterations: Annotated[list[dict[str, Any]], Field(max_length=128)] | None = None
    speed: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def reject_server_tools(self) -> "ClaudeCLIUsage":
        if any(value != 0 for value in (self.server_tool_use or {}).values()):
            raise ValueError("Claude CLI used a forbidden server tool")
        return self


class ClaudeCLIModelUsage(_StrictModel):
    inputTokens: Annotated[int, Field(ge=0)] | None = None
    outputTokens: Annotated[int, Field(ge=0)] | None = None
    cacheCreationInputTokens: Annotated[int, Field(ge=0)] | None = None
    cacheReadInputTokens: Annotated[int, Field(ge=0)] | None = None
    costUSD: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    contextWindow: Annotated[int, Field(ge=1)] | None = None
    webSearchRequests: Annotated[int, Field(ge=0)] | None = None
    maxOutputTokens: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def reject_web_search(self) -> "ClaudeCLIModelUsage":
        if self.webSearchRequests not in (None, 0):
            raise ValueError("Claude CLI used forbidden web search")
        return self


class _ClaudeCLIResult(_StrictModel):
    """Accepted subset of Claude Code's single-result JSON wrapper."""

    type: Literal["result"]
    subtype: Literal["success"]
    is_error: Literal[False]
    duration_ms: Annotated[int, Field(ge=0, le=3_600_000)]
    duration_api_ms: Annotated[int, Field(ge=0, le=3_600_000)] | None = None
    ttft_ms: Annotated[int, Field(ge=0, le=3_600_000)] | None = None
    ttft_stream_ms: Annotated[int, Field(ge=0, le=3_600_000)] | None = None
    time_to_request_ms: Annotated[int, Field(ge=0, le=3_600_000)] | None = None
    time_to_request_from_spawn_ms: Annotated[int, Field(ge=0, le=3_600_000)] | None = (
        None
    )
    warm_spare_claimed: bool | None = None
    time_origin_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    num_turns: Literal[1]
    result: Annotated[str, Field(min_length=1, max_length=MAX_RESPONSE_CHARS)]
    stop_reason: (
        Annotated[
            str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
        ]
        | None
    )
    api_error_status: Literal[None] = None
    session_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    total_cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    usage: ClaudeCLIUsage | None = None
    modelUsage: (
        Annotated[dict[str, ClaudeCLIModelUsage], Field(max_length=4)] | None
    ) = None
    permission_denials: Annotated[
        list[dict[str, Any]], Field(default_factory=list, max_length=0)
    ]
    terminal_reason: (
        Annotated[
            str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
        ]
        | None
    ) = None
    fast_mode_state: Literal["off", "cooldown", "on"] | None = None
    uuid: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def validate_models(self) -> "_ClaudeCLIResult":
        for model in self.modelUsage or {}:
            if not is_safe_model_id(model) or not model.startswith("claude-"):
                raise ValueError("Claude CLI reported an invalid provider model")
        if self.stop_reason in {"tool_use", "tool_deferred"}:
            raise ValueError("Claude CLI attempted a forbidden tool transition")
        return self


@dataclass(frozen=True, slots=True)
class CLIProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_ms: int


class CLIProcessRunner(Protocol):
    async def __call__(
        self,
        *,
        argv: tuple[str, ...],
        stdin_bytes: bytes,
        cwd: str,
        env: Mapping[str, str],
        timeout_seconds: float,
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
    ) -> CLIProcessResult: ...


class _ProcessTimeout(Exception):
    pass


class _ProcessOutputLimit(Exception):
    pass


async def _read_bounded(
    stream: asyncio.StreamReader,
    *,
    limit: int,
) -> bytes:
    body = bytearray()
    while True:
        chunk = await stream.read(min(64 * 1024, limit + 1))
        if not chunk:
            return bytes(body)
        if len(body) + len(chunk) > limit:
            raise _ProcessOutputLimit
        body.extend(chunk)


async def _write_stdin(
    stream: asyncio.StreamWriter,
    content: bytes,
) -> None:
    try:
        stream.write(content)
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stream.close()
        try:
            await stream.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    try:
        pid = getattr(process, "pid", None)
        used_process_group = False
        if isinstance(pid, int) and pid > 0 and hasattr(os, "killpg"):
            try:
                os.killpg(pid, signal.SIGKILL)
                used_process_group = True
            except AttributeError:
                pass
        if not used_process_group:
            process.kill()
    except (ProcessLookupError, OSError):
        pass
    wait_task = asyncio.create_task(process.wait())
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.5)
    except (TimeoutError, ProcessLookupError, OSError):
        pass
    finally:
        if not wait_task.done():
            wait_task.cancel()
        await asyncio.gather(wait_task, return_exceptions=True)


async def _run_bounded_process(
    *,
    argv: tuple[str, ...],
    stdin_bytes: bytes,
    cwd: str,
    env: Mapping[str, str],
    timeout_seconds: float,
    stdout_limit_bytes: int,
    stderr_limit_bytes: int,
) -> CLIProcessResult:
    """Run one direct-argv child with bounded pipes and no shell."""

    started = time.monotonic()
    try:
        process = await create_scrubbed_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            start_new_session=True,
        )
    except Exception:
        raise ProviderTransportError(
            ProviderId.ANTHROPIC, "process_launch_failed"
        ) from None
    if process.stdin is None or process.stdout is None or process.stderr is None:
        await _kill_and_reap(process)
        raise ProviderTransportError(ProviderId.ANTHROPIC, "process_pipe_unavailable")

    tasks = [
        asyncio.create_task(_write_stdin(process.stdin, stdin_bytes)),
        asyncio.create_task(_read_bounded(process.stdout, limit=stdout_limit_bytes)),
        asyncio.create_task(_read_bounded(process.stderr, limit=stderr_limit_bytes)),
        asyncio.create_task(process.wait()),
    ]
    try:
        async with asyncio.timeout(timeout_seconds):
            _, stdout, stderr, returncode = await asyncio.gather(*tasks)
    except TimeoutError:
        await _kill_and_reap(process)
        raise _ProcessTimeout from None
    except _ProcessOutputLimit:
        await _kill_and_reap(process)
        raise
    except asyncio.CancelledError:
        await _kill_and_reap(process)
        raise
    except Exception:
        await _kill_and_reap(process)
        raise ProviderTransportError(ProviderId.ANTHROPIC, "process_failed") from None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return CLIProcessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
    )


_PROVIDER_API_KEY_RE = re.compile(r"(?:^|_)(?:API|MANAGEMENT)_KEY$")
_FORBIDDEN_CLAUDE_ENV = frozenset(
    {
        "ANTHROPIC_AUTH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_ACCESS_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION",
    }
)
_ALLOWED_CLAUDE_ENV = frozenset(
    {
        "HOME",
        "PATH",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
    }
)


def _claude_subscription_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Preserve host OAuth access while removing every API fallback input."""

    clean: dict[str, str] = {}
    for raw_name, raw_value in environ.items():
        name = str(raw_name)
        upper_name = name.upper()
        if upper_name in _FORBIDDEN_CLAUDE_ENV or _PROVIDER_API_KEY_RE.search(
            upper_name
        ):
            continue
        if upper_name in _ALLOWED_CLAUDE_ENV or upper_name.startswith("LC_"):
            clean[name] = str(raw_value)
    clean.update(
        {
            "CLAUDE_CODE_SAFE_MODE": "1",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_PLUGIN_AUTOLOAD": "1",
            "DISABLE_TELEMETRY": "1",
        }
    )
    return clean


def _claude_prompt(request: ProviderRequest) -> bytes:
    from .contracts import model_visible_messages

    payload = {
        "role": "subordinate_worker",
        "authority": {
            "may_route": False,
            "may_verify": False,
            "may_harvest": False,
            "may_finalize": False,
        },
        "messages": [
            message.model_dump(mode="json")
            for message in model_visible_messages(request)
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _claude_argv(executable: str, model: str) -> tuple[str, ...]:
    return (
        executable,
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "json",
        "--model",
        model,
        "--system-prompt",
        CLAUDE_WORKER_SYSTEM_PROMPT,
        "--safe-mode",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-chrome",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--prompt-suggestions",
        "false",
    )


class ClaudeCLIAdapter(HTTPProviderAdapter):
    """One-shot Claude Code OAuth worker with tools and persistence disabled."""

    provider = ProviderId.ANTHROPIC
    channel = ProviderChannel.CLAUDE_CLI

    def __init__(
        self,
        *,
        executable: str = "claude",
        environ: Mapping[str, str] | None = None,
        runner: CLIProcessRunner | None = None,
        models: ProviderModelPins = CLAUDE_SUBSCRIPTION_MODELS,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(environ=environ, clock=clock)
        executable_path = Path(executable)
        if (
            "\x00" in executable
            or executable_path.name != "claude"
            or (executable != "claude" and not executable_path.is_absolute())
        ):
            raise ProviderConfigurationError(self.provider, "invalid_cli_executable")
        self._executable = executable
        self._runner = runner or _run_bounded_process
        self._child_env = _claude_subscription_environment(self._environ)
        self._descriptor = ProviderDescriptor(
            provider=self.provider,
            channel=self.channel,
            credential_plane=CredentialPlane.SUBSCRIPTION,
            display_name="Anthropic Claude subscription",
            endpoint_host=LOCAL_PROCESS_ENDPOINT,
            endpoint_kind="local_cli",
            credential_kind="host_oauth",
            billing_class="subscription",
            transport_resource_identity=(
                transport_resource_identity(
                    "claude_cli_executable",
                    str(executable_path),
                )
                if executable_path.is_absolute()
                else None
            ),
            credential_env_names=(),
            # Availability is learned only by the bounded call.  Construction
            # never probes the binary, keychain, or OAuth files. An unresolved
            # PATH command remains standalone-only; broker plans require an
            # absolute executable identity.
            credential_state=CredentialState.DEFERRED,
            models=models,
            max_output_tokens=32_768,
            max_timeout_seconds=120.0,
            data_handling="provider_managed",
            residency="host_subscription",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def _complete(self, request: ProviderRequest) -> ProviderResponse:
        model = self._model(request)
        _, timeout = self._limits(request)
        argv = _claude_argv(self._executable, model)
        child_env = dict(self._child_env)
        prompt = _claude_prompt(request)
        try:
            with tempfile.TemporaryDirectory(prefix="unigrok-claude-worker-") as cwd:
                result = await self._runner(
                    argv=argv,
                    stdin_bytes=prompt,
                    cwd=cwd,
                    env=child_env,
                    timeout_seconds=timeout,
                    stdout_limit_bytes=MAX_CLI_STDOUT_BYTES,
                    stderr_limit_bytes=MAX_CLI_STDERR_BYTES,
                )
        except _ProcessTimeout:
            raise ProviderTransportError(self.provider, "timeout") from None
        except _ProcessOutputLimit:
            raise ProviderProtocolError(self.provider, "response_too_large") from None
        except (ProviderTransportError, ProviderProtocolError):
            raise
        except Exception:
            raise ProviderTransportError(self.provider, "process_failed") from None
        if result.returncode != 0:
            raise ProviderTransportError(self.provider, "process_failed")
        if (
            len(result.stdout) > MAX_CLI_STDOUT_BYTES
            or len(result.stderr) > MAX_CLI_STDERR_BYTES
        ):
            raise ProviderProtocolError(self.provider, "response_too_large")
        try:
            raw = json.loads(
                result.stdout.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
            wrapper = _ClaudeCLIResult.model_validate(raw)
        except (
            UnicodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
            RecursionError,
        ):
            raise ProviderProtocolError(self.provider, "invalid_cli_json") from None

        model_names = tuple((wrapper.modelUsage or {}).keys())
        if len(model_names) > 1:
            raise ProviderProtocolError(self.provider, "ambiguous_reported_model")
        resolved_model = model_names[0] if model_names else model
        model_source: Literal["provider_reported", "requested_fallback"] = (
            "provider_reported" if model_names else "requested_fallback"
        )
        model_usage = (wrapper.modelUsage or {}).get(resolved_model)
        if model_usage is not None:
            usage = self._usage(model_usage.inputTokens, model_usage.outputTokens)
        elif wrapper.usage is not None:
            usage = self._usage(wrapper.usage.input_tokens, wrapper.usage.output_tokens)
        else:
            usage = ProviderTokenUsage()
        receipt = self._receipt(
            request=request,
            requested_model=model,
            resolved_model=resolved_model,
            model_source=model_source,
            response_id=self._response_id(wrapper.uuid),
            duration_ms=result.duration_ms,
            usage=usage,
            region="host_subscription",
        )
        return ProviderResponse(
            provider=self.provider,
            channel=self.channel,
            model=resolved_model,
            text=wrapper.result,
            finish_reason=(
                "length"
                if wrapper.stop_reason == "max_tokens"
                else "content_filter"
                if wrapper.stop_reason == "refusal"
                else "stop"
            ),
            receipt=receipt,
        )


class SamplingCapability(_StrictModel):
    client_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
    ]
    sampling: bool


class SamplingModelHint(_StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=192)]


class SamplingModelPreferences(_StrictModel):
    hints: Annotated[tuple[SamplingModelHint, ...], Field(min_length=1, max_length=4)]
    intelligence_priority: Annotated[
        float, Field(ge=0.0, le=1.0, alias="intelligencePriority")
    ] = 1.0
    speed_priority: Annotated[float, Field(ge=0.0, le=1.0, alias="speedPriority")] = 0.0
    cost_priority: Annotated[float, Field(ge=0.0, le=1.0, alias="costPriority")] = 0.0


class SamplingTextContent(_StrictModel):
    type: Literal["text"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_RESPONSE_CHARS)]


class SamplingMessage(_StrictModel):
    role: Literal["user", "assistant"]
    content: SamplingTextContent


class ClientSamplingRequest(_StrictModel):
    method: Literal["sampling/createMessage"] = "sampling/createMessage"
    messages: Annotated[
        tuple[SamplingMessage, ...], Field(min_length=1, max_length=100)
    ]
    system_prompt: (
        Annotated[str, Field(min_length=1, max_length=MAX_REQUEST_CHARS)] | None
    ) = Field(default=None, alias="systemPrompt")
    include_context: Annotated[Literal["none"], Field(alias="includeContext")] = "none"
    max_tokens: Annotated[int, Field(ge=1, le=32_768, alias="maxTokens")]
    model_preferences: Annotated[
        SamplingModelPreferences, Field(alias="modelPreferences")
    ]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None


class ClientSamplingResult(_StrictModel):
    role: Literal["assistant"] = "assistant"
    content: SamplingTextContent
    model: Annotated[str, Field(min_length=1, max_length=192)]
    stop_reason: Annotated[
        Literal["endTurn", "stopSequence", "maxTokens", "contentFilter"],
        Field(alias="stopReason"),
    ]
    usage: ProviderTokenUsage = ProviderTokenUsage()
    cost_usd: (
        Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=8)] | None
    ) = None
    response_id: (
        Annotated[
            str,
            Field(
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
            ),
        ]
        | None
    ) = None


SamplingCallback = Callable[
    [ClientSamplingRequest],
    Awaitable[ClientSamplingResult | Mapping[str, Any]],
]
EffectClaimInspector = Callable[[], bool]


_SAMPLING_BINDING_FACTORY_SEAL = object()


def _snapshot_sampling_capability_descriptor(
    descriptor: ProviderDescriptor,
    *,
    provider: ProviderId,
    channel: ProviderChannel,
    capability: SamplingCapability,
    models: ProviderModelPins,
    route: RouteClass,
) -> ProviderDescriptor:
    try:
        snapshot = ProviderDescriptor.model_validate_json(
            descriptor.model_dump_json(warnings="error")
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise ValueError("sampling capability descriptor is invalid") from None
    client_identity = snapshot.client_identity or ""
    capability_digest = (
        f"sha256:{client_identity.removeprefix('mcp-')}"
        if re.fullmatch(r"mcp-[0-9a-f]{64}", client_identity)
        else ""
    )
    expected_transport_identity = (
        transport_resource_identity("mcp_sampling_capability", capability_digest)
        if capability_digest
        else None
    )
    if (
        snapshot.provider != provider
        or snapshot.channel != channel
        or snapshot.credential_plane != CredentialPlane.SUBSCRIPTION
        or snapshot.endpoint_host != MCP_CLIENT_ENDPOINT
        or snapshot.endpoint_kind != "mcp_client_sampling"
        or snapshot.credential_kind != "mcp_client_subscription"
        or snapshot.billing_class != "subscription"
        or snapshot.client_identity != capability.client_id
        or snapshot.display_name != f"{provider.value} IDE subscription"
        or snapshot.transport_resource_identity != expected_transport_identity
        or snapshot.credential_env_names
        or snapshot.credential_state != CredentialState.DEFERRED
        or snapshot.models != models
        or route not in snapshot.supported_routes
        or snapshot.data_handling != "provider_managed"
        or snapshot.residency != "client_subscription"
        or snapshot.supports_normalized_tools
    ):
        raise ValueError("sampling capability descriptor is invalid")
    return snapshot


@dataclass(frozen=True, slots=True, init=False)
class SamplingClientBinding:
    capability: SamplingCapability
    callback: SamplingCallback
    provider: ProviderId
    channel: ProviderChannel
    models: ProviderModelPins
    descriptor: ProviderDescriptor
    binding_digest: str
    supervision: GrokSupervisorBinding
    provider_request_id: str
    provider_request_digest: str
    route: RouteClass
    effect_claimed: EffectClaimInspector
    delegation_digest: str

    def __init__(
        self,
        *,
        capability: SamplingCapability,
        callback: SamplingCallback,
        provider: ProviderId,
        channel: ProviderChannel,
        models: ProviderModelPins,
        descriptor: ProviderDescriptor,
        binding_digest: str,
        supervision: GrokSupervisorBinding,
        provider_request_id: str,
        provider_request_digest: str,
        route: RouteClass,
        effect_claimed: EffectClaimInspector,
        _factory_seal: object | None = None,
    ) -> None:
        if _factory_seal is not _SAMPLING_BINDING_FACTORY_SEAL:
            raise TypeError(
                "SamplingClientBinding must be created by the stateful MCP sampling factory"
            )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", binding_digest):
            raise ValueError("sampling binding digest must be a SHA-256 identity")
        if not capability.sampling:
            raise ValueError("sampling capability does not match sealed binding")
        if SAMPLING_CHANNEL_PROVIDERS.get(channel) != provider:
            raise ValueError("sealed sampling provider and channel do not match")
        try:
            supervision_snapshot = GrokSupervisorBinding.model_validate_json(
                supervision.model_dump_json()
            )
            models_snapshot = ProviderModelPins.model_validate_json(
                models.model_dump_json()
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise ValueError("sampling grant binding is invalid") from None
        if not isinstance(supervision_snapshot, GrokSupervisorBinding):
            raise ValueError("sampling supervision binding is invalid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", provider_request_id):
            raise ValueError("sampling provider request id is invalid")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", provider_request_digest):
            raise ValueError("sampling provider request digest is invalid")
        if not isinstance(route, RouteClass):
            raise ValueError("sampling route is invalid")
        if not callable(effect_claimed):
            raise ValueError("sampling effect inspector is invalid")
        descriptor_snapshot = _snapshot_sampling_capability_descriptor(
            descriptor,
            provider=provider,
            channel=channel,
            capability=capability,
            models=models_snapshot,
            route=route,
        )
        delegation_digest = _sampling_delegation_digest(
            binding_digest=binding_digest,
            provider=provider,
            channel=channel,
            supervision=supervision_snapshot,
            provider_request_id=provider_request_id,
            provider_request_digest=provider_request_digest,
            route=route,
            authorized_model=models_snapshot.for_route(route),
        )
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "callback", callback)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "models", models_snapshot)
        object.__setattr__(self, "descriptor", descriptor_snapshot)
        object.__setattr__(self, "binding_digest", binding_digest)
        object.__setattr__(self, "supervision", supervision_snapshot)
        object.__setattr__(self, "provider_request_id", provider_request_id)
        object.__setattr__(self, "provider_request_digest", provider_request_digest)
        object.__setattr__(self, "route", route)
        object.__setattr__(self, "effect_claimed", effect_claimed)
        object.__setattr__(self, "delegation_digest", delegation_digest)


def _sampling_delegation_digest(
    *,
    binding_digest: str,
    provider: ProviderId,
    channel: ProviderChannel,
    supervision: GrokSupervisorBinding,
    provider_request_id: str,
    provider_request_digest: str,
    route: RouteClass,
    authorized_model: str,
) -> str:
    payload = {
        "binding_digest": binding_digest,
        "provider": provider.value,
        "channel": channel.value,
        "supervision": supervision.model_dump(mode="json"),
        "provider_request_id": provider_request_id,
        "provider_request_digest": provider_request_digest,
        "route": route.value,
        "authorized_model": authorized_model,
    }
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _create_sealed_sampling_client_binding(
    *,
    capability: SamplingCapability,
    callback: SamplingCallback,
    provider: ProviderId,
    channel: ProviderChannel,
    models: ProviderModelPins,
    descriptor: ProviderDescriptor,
    binding_digest: str,
    supervision: GrokSupervisorBinding,
    provider_request_id: str,
    provider_request_digest: str,
    route: RouteClass,
    effect_claimed: EffectClaimInspector,
) -> SamplingClientBinding:
    """Internal constructor used only by the stateful request lease."""

    return SamplingClientBinding(
        capability=capability,
        callback=callback,
        provider=provider,
        channel=channel,
        models=models,
        descriptor=descriptor,
        binding_digest=binding_digest,
        supervision=supervision,
        provider_request_id=provider_request_id,
        provider_request_digest=provider_request_digest,
        route=route,
        effect_claimed=effect_claimed,
        _factory_seal=_SAMPLING_BINDING_FACTORY_SEAL,
    )


def provider_request_digest(request: ProviderRequest) -> str:
    """Canonical secret-safe digest of one complete provider request."""

    snapshot = ProviderRequest.model_validate_json(
        request.model_dump_json(warnings="error")
    )
    material = json.dumps(
        snapshot.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(material).hexdigest()


@dataclass(frozen=True, slots=True)
class _SamplingAdapterAuthority:
    provider: ProviderId
    channel: ProviderChannel
    capability: SamplingCapability
    callback: SamplingCallback
    effect_claimed: EffectClaimInspector
    models: ProviderModelPins
    binding_digest: str
    supervision: GrokSupervisorBinding
    provider_request_id: str
    provider_request_digest: str
    route: RouteClass
    delegation_digest: str
    descriptor: ProviderDescriptor
    clock: Clock


def _sampling_adapter_authority_digest(authority: _SamplingAdapterAuthority) -> str:
    payload = {
        "provider": authority.provider.value,
        "channel": authority.channel.value,
        "capability": authority.capability.model_dump(mode="json"),
        "callback_identity": id(authority.callback),
        "effect_claimed_identity": id(authority.effect_claimed),
        "models": authority.models.model_dump(mode="json"),
        "binding_digest": authority.binding_digest,
        "supervision": authority.supervision.model_dump(mode="json"),
        "provider_request_id": authority.provider_request_id,
        "provider_request_digest": authority.provider_request_digest,
        "route": authority.route.value,
        "delegation_digest": authority.delegation_digest,
        "descriptor": authority.descriptor.model_dump(mode="json"),
        "clock_identity": id(authority.clock),
    }
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(material).hexdigest()


class MCPClientSamplingAdapter(HTTPProviderAdapter):
    """One lease-owned MCP sampling lane bound to one trusted provider grant.

    The private authority state is sealed against ordinary assignment and
    rechecked before and after every await.  Arbitrary code execution inside
    the trusted server process remains outside this object's security boundary.
    """

    _PROTECTED_AUTHORITY_ATTRIBUTES = frozenset({"provider", "channel", "_clock"})

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sampling_adapter_sealed", False) and (
            name in self._PROTECTED_AUTHORITY_ATTRIBUTES
            or name.startswith("_sampling_")
        ):
            raise AttributeError("sealed MCP sampling authority cannot be mutated")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        provider: ProviderId,
        channel: ProviderChannel,
        binding: SamplingClientBinding,
        clock: Clock | None = None,
        _factory_seal: object | None = None,
    ) -> None:
        if _factory_seal is not _MCP_SAMPLING_ADAPTER_FACTORY_SEAL:
            raise TypeError(
                "MCPClientSamplingAdapter must be created by the stateful MCP sampling lease"
            )
        super().__init__(environ={}, clock=clock)
        if SAMPLING_CHANNEL_PROVIDERS.get(channel) != provider:
            raise ProviderConfigurationError(provider, "sampling_channel_mismatch")
        if binding.provider != provider or binding.channel != channel:
            raise ProviderConfigurationError(provider, "sampling_grant_mismatch")
        try:
            capability = SamplingCapability.model_validate_json(
                binding.capability.model_dump_json(warnings="error")
            )
            models = ProviderModelPins.model_validate_json(
                binding.models.model_dump_json(warnings="error")
            )
            supervision = GrokSupervisorBinding.model_validate_json(
                binding.supervision.model_dump_json(warnings="error")
            )
            expected_delegation_digest = _sampling_delegation_digest(
                binding_digest=binding.binding_digest,
                provider=binding.provider,
                channel=binding.channel,
                supervision=supervision,
                provider_request_id=binding.provider_request_id,
                provider_request_digest=binding.provider_request_digest,
                route=binding.route,
                authorized_model=models.for_route(binding.route),
            )
            descriptor = _snapshot_sampling_capability_descriptor(
                binding.descriptor,
                provider=provider,
                channel=channel,
                capability=capability,
                models=models,
                route=binding.route,
            )
        except Exception:
            raise ProviderConfigurationError(
                provider, "sampling_grant_mismatch"
            ) from None
        if (
            expected_delegation_digest != binding.delegation_digest
            or capability.client_id != binding.descriptor.client_identity
            or not capability.sampling
            or not callable(binding.callback)
            or not callable(binding.effect_claimed)
        ):
            raise ProviderConfigurationError(provider, "sampling_grant_mismatch")
        authority = _SamplingAdapterAuthority(
            provider=provider,
            channel=channel,
            capability=capability,
            callback=binding.callback,
            effect_claimed=binding.effect_claimed,
            models=models,
            binding_digest=str(binding.binding_digest),
            supervision=supervision,
            provider_request_id=str(binding.provider_request_id),
            provider_request_digest=str(binding.provider_request_digest),
            route=binding.route,
            delegation_digest=str(binding.delegation_digest),
            descriptor=descriptor,
            clock=self._clock,
        )
        self.provider = provider
        self.channel = channel
        self._sampling_authority = authority
        self._sampling_authority_identity = id(authority)
        self._sampling_authority_digest = _sampling_adapter_authority_digest(authority)
        self._sampling_adapter_sealed = True

    def _validated_authority(self) -> _SamplingAdapterAuthority:
        try:
            authority = object.__getattribute__(self, "_sampling_authority")
            valid = (
                isinstance(authority, _SamplingAdapterAuthority)
                and id(authority)
                == object.__getattribute__(self, "_sampling_authority_identity")
                and _sampling_adapter_authority_digest(authority)
                == object.__getattribute__(self, "_sampling_authority_digest")
                and object.__getattribute__(self, "provider") == authority.provider
                and object.__getattribute__(self, "channel") == authority.channel
                and object.__getattribute__(self, "_clock") is authority.clock
            )
        except Exception:
            valid = False
            authority = None
        if not valid or authority is None:
            provider = getattr(self, "provider", ProviderId.XAI)
            if not isinstance(provider, ProviderId):
                provider = ProviderId.XAI
            raise ProviderAuthorizationInvariantError(
                provider, "sampling_adapter_authority_mutated"
            )
        return authority

    @staticmethod
    def _claimed(authority: _SamplingAdapterAuthority) -> bool:
        try:
            claimed = authority.effect_claimed()
        except Exception:
            return True
        return claimed if isinstance(claimed, bool) else True

    @staticmethod
    def _snapshot_request(
        request: ProviderRequest, provider: ProviderId
    ) -> ProviderRequest:
        try:
            if not isinstance(request, ProviderRequest):
                raise TypeError
            return ProviderRequest.model_validate_json(
                request.model_dump_json(warnings="error")
            )
        except Exception:
            raise ProviderConfigurationError(
                provider, "sampling_provider_request_invalid"
            ) from None

    @property
    def descriptor(self) -> ProviderDescriptor:
        authority = self._validated_authority()
        return ProviderDescriptor.model_validate_json(
            authority.descriptor.model_dump_json(warnings="error")
        )

    def effect_claimed(self) -> bool:
        """Return fail-closed one-shot state from the lease-owned runtime."""

        try:
            authority = self._validated_authority()
        except ProviderAuthorizationInvariantError:
            return True
        return self._claimed(authority)

    async def attempt(self, request: ProviderRequest) -> ProviderAttemptResult:
        authority = self._validated_authority()
        request_snapshot = self._snapshot_request(request, authority.provider)
        receipt_descriptor = ProviderDescriptor.model_validate_json(
            authority.descriptor.model_dump_json(warnings="error")
        )
        receipt_provider = authority.provider
        receipt_channel = authority.channel
        started = time.monotonic()
        requested_model = request_snapshot.model or authority.models.for_route(
            request_snapshot.route
        )
        try:
            response = await self.complete(request_snapshot)
        except ProviderError as exc:
            return ProviderAttemptResult(
                status="failed",
                failure=self._sampling_failure_receipt(
                    request=request_snapshot,
                    requested_model=requested_model,
                    provider=receipt_provider,
                    channel=receipt_channel,
                    descriptor=receipt_descriptor,
                    error_kind=self._error_kind(exc),
                    error_code=exc.code,
                    duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                ),
            )
        except Exception:
            return ProviderAttemptResult(
                status="failed",
                failure=self._sampling_failure_receipt(
                    request=request_snapshot,
                    requested_model=requested_model,
                    provider=receipt_provider,
                    channel=receipt_channel,
                    descriptor=receipt_descriptor,
                    error_kind="internal",
                    error_code="unexpected_error",
                    duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                ),
            )
        return ProviderAttemptResult(status="returned", response=response)

    @staticmethod
    def _sampling_failure_receipt(
        *,
        request: ProviderRequest,
        requested_model: str,
        provider: ProviderId,
        channel: ProviderChannel,
        descriptor: ProviderDescriptor,
        error_kind: Literal["configuration", "transport", "protocol", "internal"],
        error_code: str,
        duration_ms: int,
    ) -> ProviderFailureReceipt:
        return ProviderFailureReceipt(
            request_id=request.request_id,
            supervision=request.supervision,
            provider=provider,
            channel=channel,
            credential_plane=descriptor.credential_plane,
            route=request.route,
            requested_model=requested_model,
            endpoint_host=descriptor.endpoint_host,
            endpoint_kind=descriptor.endpoint_kind,
            credential_kind=descriptor.credential_kind,
            billing_class=descriptor.billing_class,
            client_identity=descriptor.client_identity,
            error_kind=error_kind,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Preserve post-claim indeterminacy across the base TTL boundary.

        The future broker integration must perform the same probe around its
        own outer timeout before this adapter can be wired into runtime.
        """

        authority = self._validated_authority()
        request_snapshot = self._snapshot_request(request, authority.provider)
        try:
            response = await super().complete(request_snapshot)
            self._validated_authority()
            return response
        except asyncio.CancelledError:
            # Only pre-claim cancellation can reach this boundary.  The lease
            # converts every post-claim cancellation source to an internal,
            # indeterminate effect failure before it reaches the adapter.
            raise
        except (
            ProviderConfigurationError,
            ProviderProtocolError,
            ProviderTransportError,
        ):
            self._validated_authority()
            if self._claimed(authority):
                raise ProviderAuthorizationInvariantError(
                    authority.provider, "sampling_effect_indeterminate"
                ) from None
            raise

    def _sampling_request(
        self,
        request: ProviderRequest,
        *,
        model: str,
        max_tokens: int,
    ) -> ClientSamplingRequest:
        from .contracts import model_visible_messages

        model_messages = model_visible_messages(request)
        system_parts = [m.content for m in model_messages if m.role == "system"]
        messages = tuple(
            SamplingMessage(
                role=m.role,
                content=SamplingTextContent(type="text", text=m.content),
            )
            for m in model_messages
            if m.role != "system"
        )
        return ClientSamplingRequest(
            messages=messages,
            system_prompt="\n\n".join(system_parts) or None,
            include_context="none",
            max_tokens=max_tokens,
            model_preferences=SamplingModelPreferences(
                hints=(SamplingModelHint(name=model),),
            ),
            temperature=request.temperature,
        )

    async def _complete(self, request: ProviderRequest) -> ProviderResponse:
        authority = self._validated_authority()
        request = self._snapshot_request(request, authority.provider)
        callback = authority.callback
        descriptor = ProviderDescriptor.model_validate_json(
            authority.descriptor.model_dump_json(warnings="error")
        )
        models = ProviderModelPins.model_validate_json(
            authority.models.model_dump_json(warnings="error")
        )
        if not authority.capability.sampling:
            raise ProviderConfigurationError(authority.provider, "sampling_unavailable")
        try:
            current_digest = _sampling_delegation_digest(
                binding_digest=authority.binding_digest,
                provider=authority.provider,
                channel=authority.channel,
                supervision=authority.supervision,
                provider_request_id=authority.provider_request_id,
                provider_request_digest=authority.provider_request_digest,
                route=authority.route,
                authorized_model=models.for_route(authority.route),
            )
            requested_model = request.model or models.for_route(request.route)
            matches_request = (
                current_digest == authority.delegation_digest
                and request.supervision == authority.supervision
                and request.request_id == authority.provider_request_id
                and provider_request_digest(request)
                == authority.provider_request_digest
                and request.route == authority.route
                and requested_model == models.for_route(authority.route)
            )
        except Exception:
            matches_request = False
        if not matches_request:
            raise ProviderConfigurationError(
                authority.provider, "sampling_grant_mismatch"
            )
        model = self._model(request)
        max_tokens, timeout = self._limits(request)
        sampling_request = self._sampling_request(
            request,
            model=model,
            max_tokens=max_tokens,
        )
        self._validated_authority()
        started = time.monotonic()
        adapter_timeout: asyncio.Timeout | None = None
        try:
            async with asyncio.timeout(timeout) as adapter_timeout:
                raw_result = await callback(sampling_request)
        except TimeoutError:
            self._validated_authority()
            if self._claimed(authority):
                raise ProviderAuthorizationInvariantError(
                    authority.provider, "sampling_effect_indeterminate"
                ) from None
            raise ProviderTransportError(authority.provider, "timeout") from None
        except ProviderError:
            self._validated_authority()
            raise
        except Exception:
            self._validated_authority()
            raise ProviderTransportError(
                authority.provider, "sampling_failed"
            ) from None
        self._validated_authority()
        if adapter_timeout is not None and adapter_timeout.expired():
            if self._claimed(authority):
                raise ProviderAuthorizationInvariantError(
                    authority.provider, "sampling_effect_indeterminate"
                )
            raise ProviderTransportError(authority.provider, "timeout")
        try:
            result = (
                ClientSamplingResult.model_validate_json(
                    raw_result.model_dump_json(warnings="error")
                )
                if isinstance(raw_result, ClientSamplingResult)
                else ClientSamplingResult.model_validate(raw_result)
            )
        except Exception:
            raise ProviderAuthorizationInvariantError(
                authority.provider, "sampling_effect_indeterminate"
            ) from None
        granted_model = models.for_route(authority.route)
        if not is_safe_model_id(result.model) or result.model != granted_model:
            raise ProviderAuthorizationInvariantError(
                authority.provider, "sampling_effect_indeterminate"
            )
        finish_reason = {
            "endTurn": "stop",
            "stopSequence": "stop",
            "maxTokens": "length",
            "contentFilter": "content_filter",
        }[result.stop_reason]
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        receipt = ProviderReceipt(
            request_id=request.request_id,
            supervision=request.supervision,
            provider=authority.provider,
            channel=authority.channel,
            credential_plane=descriptor.credential_plane,
            route=request.route,
            requested_model=model,
            resolved_model=result.model,
            model_source="requested_fallback",
            endpoint_host=descriptor.endpoint_host,
            endpoint_kind=descriptor.endpoint_kind,
            credential_kind=descriptor.credential_kind,
            billing_class=descriptor.billing_class,
            client_identity=descriptor.client_identity,
            cost_usd=None,
            cost_source="unavailable",
            region="client_subscription",
            response_id=None,
            duration_ms=duration_ms,
            usage=ProviderTokenUsage(),
        )
        return ProviderResponse(
            provider=authority.provider,
            channel=authority.channel,
            model=result.model,
            text=result.content.text,
            finish_reason=finish_reason,
            receipt=receipt,
        )


_MCP_SAMPLING_ADAPTER_FACTORY_SEAL = object()


def _create_sealed_mcp_sampling_adapter(
    *,
    provider: ProviderId,
    channel: ProviderChannel,
    binding: SamplingClientBinding,
    clock: Clock | None = None,
) -> MCPClientSamplingAdapter:
    """Internal bridge from a live lease callback to one bounded adapter."""

    return MCPClientSamplingAdapter(
        provider=provider,
        channel=channel,
        binding=binding,
        clock=clock,
        _factory_seal=_MCP_SAMPLING_ADAPTER_FACTORY_SEAL,
    )


def build_subscription_registry(
    *,
    claude_executable: str = "claude",
    environ: Mapping[str, str] | None = None,
    claude_runner: CLIProcessRunner | None = None,
    clock: Clock | None = None,
) -> dict[ProviderChannel, ProviderAdapter]:
    """Construct request-scoped subscription adapters without any effect."""

    registry: dict[ProviderChannel, ProviderAdapter] = {
        ProviderChannel.CLAUDE_CLI: ClaudeCLIAdapter(
            executable=claude_executable,
            environ=environ if environ is not None else os.environ,
            runner=claude_runner,
            clock=clock,
        )
    }
    return registry
