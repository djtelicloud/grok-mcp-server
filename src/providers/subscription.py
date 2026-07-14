"""Bounded subscription transports for subordinate semantic workers.

These adapters are deliberately request-scoped and inert until ``complete`` is
called by a future Grok-owned broker.  They do not discover IDEs, inspect auth
files, copy credentials, route work, or grant a worker completion authority.
"""

from __future__ import annotations

import asyncio
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
    ProviderAdapter,
    ProviderChannel,
    ProviderDescriptor,
    ProviderId,
    ProviderModelPins,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    is_safe_model_id,
)
from .errors import (
    ProviderConfigurationError,
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

SAMPLING_DEFAULT_MODELS: dict[ProviderId, ProviderModelPins] = {
    ProviderId.OPENAI: DEFAULT_MODEL_PINS[ProviderChannel.OPENAI_API],
    ProviderId.ANTHROPIC: CLAUDE_SUBSCRIPTION_MODELS,
    ProviderId.GOOGLE: DEFAULT_MODEL_PINS[ProviderChannel.GEMINI_API_KEY],
}

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
        if isinstance(pid, int) and pid > 0:
            os.killpg(pid, signal.SIGKILL)
        else:
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
        process = await asyncio.create_subprocess_exec(
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
        self._descriptor = ProviderDescriptor(
            provider=self.provider,
            channel=self.channel,
            credential_plane=CredentialPlane.SUBSCRIPTION,
            display_name="Anthropic Claude subscription",
            endpoint_host=LOCAL_PROCESS_ENDPOINT,
            endpoint_kind="local_cli",
            credential_kind="host_oauth",
            billing_class="subscription",
            credential_env_names=(),
            # Availability is learned only by the bounded call.  Construction
            # never probes the binary, keychain, or OAuth files.
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
        child_env = _claude_subscription_environment(self._environ)
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
            response_id=wrapper.uuid,
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


@dataclass(frozen=True, slots=True)
class SamplingClientBinding:
    capability: SamplingCapability
    callback: SamplingCallback
    models: ProviderModelPins | None = None


def _provider_model_matches(provider: ProviderId, model: str) -> bool:
    prefixes = {
        ProviderId.OPENAI: ("gpt-", "o1", "o3", "o4", "codex-", "chatgpt-"),
        ProviderId.ANTHROPIC: ("claude-",),
        ProviderId.GOOGLE: ("gemini-",),
    }
    return provider in prefixes and model.startswith(prefixes[provider])


class MCPClientSamplingAdapter(HTTPProviderAdapter):
    """One client-advertised MCP sampling lane bound to one trusted provider."""

    def __init__(
        self,
        *,
        provider: ProviderId,
        channel: ProviderChannel,
        binding: SamplingClientBinding,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(environ={}, clock=clock)
        if SAMPLING_CHANNEL_PROVIDERS.get(channel) != provider:
            raise ProviderConfigurationError(provider, "sampling_channel_mismatch")
        self.provider = provider
        self.channel = channel
        self._binding = binding
        models = binding.models or SAMPLING_DEFAULT_MODELS[provider]
        self._descriptor = ProviderDescriptor(
            provider=provider,
            channel=channel,
            credential_plane=CredentialPlane.SUBSCRIPTION,
            display_name=f"{provider.value} IDE subscription",
            endpoint_host=MCP_CLIENT_ENDPOINT,
            endpoint_kind="mcp_client_sampling",
            credential_kind="mcp_client_subscription",
            billing_class="subscription",
            client_identity=binding.capability.client_id,
            credential_env_names=(),
            credential_state=(
                CredentialState.DEFERRED
                if binding.capability.sampling
                else CredentialState.MISSING
            ),
            models=models,
            max_output_tokens=32_768,
            max_timeout_seconds=120.0,
            data_handling="provider_managed",
            residency="client_subscription",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

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
        if not self._binding.capability.sampling:
            raise ProviderConfigurationError(self.provider, "sampling_unavailable")
        model = self._model(request)
        max_tokens, timeout = self._limits(request)
        sampling_request = self._sampling_request(
            request,
            model=model,
            max_tokens=max_tokens,
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                raw_result = await self._binding.callback(sampling_request)
        except TimeoutError:
            raise ProviderTransportError(self.provider, "timeout") from None
        except (
            ProviderConfigurationError,
            ProviderProtocolError,
            ProviderTransportError,
        ):
            raise
        except Exception:
            raise ProviderTransportError(self.provider, "sampling_failed") from None
        try:
            result = (
                raw_result
                if isinstance(raw_result, ClientSamplingResult)
                else ClientSamplingResult.model_validate(raw_result)
            )
        except (ValidationError, ValueError, TypeError):
            raise ProviderProtocolError(
                self.provider, "invalid_sampling_result"
            ) from None
        if not is_safe_model_id(result.model) or not _provider_model_matches(
            self.provider, result.model
        ):
            raise ProviderProtocolError(self.provider, "provider_model_mismatch")
        finish_reason = {
            "endTurn": "stop",
            "stopSequence": "stop",
            "maxTokens": "length",
            "contentFilter": "content_filter",
        }[result.stop_reason]
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        descriptor = self.descriptor
        receipt = ProviderReceipt(
            request_id=request.request_id,
            supervision=request.supervision,
            provider=self.provider,
            channel=self.channel,
            credential_plane=descriptor.credential_plane,
            route=request.route,
            requested_model=model,
            resolved_model=result.model,
            model_source="provider_reported",
            endpoint_host=descriptor.endpoint_host,
            endpoint_kind=descriptor.endpoint_kind,
            credential_kind=descriptor.credential_kind,
            billing_class=descriptor.billing_class,
            client_identity=descriptor.client_identity,
            cost_usd=result.cost_usd,
            cost_source="provider_exact"
            if result.cost_usd is not None
            else "unavailable",
            region="client_subscription",
            response_id=result.response_id,
            duration_ms=duration_ms,
            usage=result.usage,
        )
        return ProviderResponse(
            provider=self.provider,
            channel=self.channel,
            model=result.model,
            text=result.content.text,
            finish_reason=finish_reason,
            receipt=receipt,
        )


def build_subscription_registry(
    *,
    claude_executable: str = "claude",
    environ: Mapping[str, str] | None = None,
    claude_runner: CLIProcessRunner | None = None,
    sampling_clients: Mapping[ProviderChannel, SamplingClientBinding] | None = None,
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
    for channel, binding in (sampling_clients or {}).items():
        provider = SAMPLING_CHANNEL_PROVIDERS.get(channel)
        if provider is None:
            raise ValueError("unsupported sampling channel")
        registry[channel] = MCPClientSamplingAdapter(
            provider=provider,
            channel=channel,
            binding=binding,
            clock=clock,
        )
    return registry
