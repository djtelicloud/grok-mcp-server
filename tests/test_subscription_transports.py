from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.providers import (
    DISABLED_SUBSCRIPTION_SURFACES,
    CLIProcessResult,
    ClaudeCLIAdapter,
    ClientSamplingResult,
    CredentialPlane,
    CredentialState,
    GrokSupervisorBinding,
    GrokWorkerLaneAuthorization,
    MCPClientSamplingAdapter,
    ProviderAttemptStart,
    ProviderChannel,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderId,
    ProviderMessage,
    ProviderModelPins,
    ProviderReceipt,
    ProviderRequest,
    ProviderTokenUsage,
    RouteClass,
    SamplingCapability,
    SamplingTextContent,
    build_subscription_registry,
    provider_request_digest,
    transport_resource_identity,
)
from src.providers.subscription import (
    MAX_CLI_STDERR_BYTES,
    MAX_CLI_STDOUT_BYTES,
    _ProcessOutputLimit,
    _ProcessTimeout,
    SamplingClientBinding,
    _create_sealed_mcp_sampling_adapter,
    _create_sealed_sampling_client_binding,
    _claude_subscription_environment,
    _run_bounded_process,
)


def _request(
    *,
    now: datetime | None = None,
    ttl_seconds: float = 30.0,
    model: str | None = None,
    timeout_seconds: float = 30.0,
    max_output_tokens: int = 4096,
) -> ProviderRequest:
    current = now or datetime.now(UTC)
    return ProviderRequest(
        request_id="subscription-request-1",
        supervision=GrokSupervisorBinding(
            session_id="grok-session-1",
            objective_id="objective-1",
            route_decision_id="route-1",
            ttl_expires_at=current + timedelta(seconds=ttl_seconds),
        ),
        route=RouteClass.PLANNING,
        messages=(
            ProviderMessage(role="system", content="Return a bounded observation."),
            ProviderMessage(role="user", content="Compare the two approaches."),
        ),
        model=model,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )


def _claude_result(
    *,
    text: str = "Claude subscription observation.",
    model: str = "claude-fable-5-20260701",
    extra: dict | None = None,
) -> bytes:
    value = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 19,
        "duration_api_ms": 11,
        "num_turns": 1,
        "result": text,
        "stop_reason": "end_turn",
        "api_error_status": None,
        "session_id": "00000000-0000-4000-8000-000000000001",
        "total_cost_usd": 0.0,
        "usage": {
            "input_tokens": 8,
            "output_tokens": 3,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "server_tool_use": {
                "web_search_requests": 0,
                "web_fetch_requests": 0,
            },
            "service_tier": "standard",
            "cache_creation": {
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 0,
            },
            "inference_geo": "",
            "iterations": [],
            "speed": "standard",
        },
        "modelUsage": {
            model: {
                "inputTokens": 8,
                "outputTokens": 3,
                "costUSD": 0.0,
                "contextWindow": 200_000,
                "webSearchRequests": 0,
                "maxOutputTokens": 32_768,
            }
        },
        "permission_denials": [],
        "fast_mode_state": "off",
        "uuid": "00000000-0000-4000-8000-000000000002",
    }
    value.update(extra or {})
    return json.dumps(value).encode()


class CapturingRunner:
    def __init__(self, result: CLIProcessResult | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result or CLIProcessResult(
            returncode=0,
            stdout=_claude_result(),
            stderr=b"",
            duration_ms=25,
        )

    async def __call__(self, **kwargs) -> CLIProcessResult:
        self.calls.append(kwargs)
        assert Path(kwargs["cwd"]).name.startswith("unigrok-claude-worker-")
        assert list(Path(kwargs["cwd"]).iterdir()) == []
        return self.result


def _sampling_binding(
    callback,
    *,
    request: ProviderRequest,
    provider: ProviderId = ProviderId.OPENAI,
    channel: ProviderChannel | None = None,
    client_id: str = "codex-desktop",
    sampling: bool = True,
    effect_claimed=None,
) -> SamplingClientBinding:
    channels = {
        ProviderId.OPENAI: ProviderChannel.OPENAI_MCP_SAMPLING,
        ProviderId.ANTHROPIC: ProviderChannel.ANTHROPIC_MCP_SAMPLING,
        ProviderId.GOOGLE: ProviderChannel.GOOGLE_MCP_SAMPLING,
    }
    selected_channel = channel or channels[provider]
    model = (
        request.model
        or {
            ProviderId.OPENAI: "gpt-5.1",
            ProviderId.ANTHROPIC: "claude-fable-5",
            ProviderId.GOOGLE: "gemini-3.5-flash",
        }[provider]
    )
    models = ProviderModelPins(
        planning=model,
        coding=model,
        vision=model,
        research=model,
    )
    binding_digest = transport_resource_identity(
        "test_mcp_sampling_binding",
        f"{provider.value}:{selected_channel.value}:{client_id}:{request.request_id}",
    )
    capability_digest = transport_resource_identity(
        "test_mcp_sampling_capability",
        f"{provider.value}:{selected_channel.value}:{client_id}",
    )
    stable_client_identity = "mcp-" + capability_digest.removeprefix("sha256:")
    descriptor = ProviderDescriptor(
        provider=provider,
        channel=selected_channel,
        credential_plane=CredentialPlane.SUBSCRIPTION,
        display_name=f"{provider.value} IDE subscription",
        endpoint_host="mcp-client",
        endpoint_kind="mcp_client_sampling",
        credential_kind="mcp_client_subscription",
        billing_class="subscription",
        client_identity=stable_client_identity,
        transport_resource_identity=transport_resource_identity(
            "mcp_sampling_capability", capability_digest
        ),
        credential_env_names=(),
        credential_state=CredentialState.DEFERRED,
        models=models,
        supported_routes=(request.route,),
        max_output_tokens=32_768,
        max_timeout_seconds=120.0,
        data_handling="provider_managed",
        residency="client_subscription",
    )
    return _create_sealed_sampling_client_binding(
        capability=SamplingCapability(
            client_id=stable_client_identity,
            sampling=sampling,
        ),
        callback=callback,
        provider=provider,
        channel=selected_channel,
        models=models,
        descriptor=descriptor,
        binding_digest=binding_digest,
        supervision=request.supervision,
        provider_request_id=request.request_id,
        provider_request_digest=provider_request_digest(request),
        route=request.route,
        effect_claimed=effect_claimed or (lambda: False),
    )


def _sampling_adapter(
    callback,
    *,
    request: ProviderRequest,
    provider: ProviderId = ProviderId.OPENAI,
    channel: ProviderChannel | None = None,
    client_id: str = "codex-desktop",
    sampling: bool = True,
    effect_claimed=None,
    clock=None,
) -> MCPClientSamplingAdapter:
    selected_channel = (
        channel
        or {
            ProviderId.OPENAI: ProviderChannel.OPENAI_MCP_SAMPLING,
            ProviderId.ANTHROPIC: ProviderChannel.ANTHROPIC_MCP_SAMPLING,
            ProviderId.GOOGLE: ProviderChannel.GOOGLE_MCP_SAMPLING,
        }[provider]
    )
    return _create_sealed_mcp_sampling_adapter(
        provider=provider,
        channel=selected_channel,
        binding=_sampling_binding(
            callback,
            request=request,
            provider=provider,
            channel=selected_channel,
            client_id=client_id,
            sampling=sampling,
            effect_claimed=effect_claimed,
        ),
        clock=clock,
    )


def test_subscription_attempt_identity_is_exact_and_api_invariants_remain_strict():
    request = _request(model="claude-fable-5")
    start = ProviderAttemptStart(
        attempt_id="attempt-1",
        delegation_id="delegation-1",
        attempt_ordinal=1,
        supervisor_plane="CLI",
        supervisor_model="grok-4.5",
        provider=ProviderId.ANTHROPIC,
        channel=ProviderChannel.CLAUDE_CLI,
        credential_plane=CredentialPlane.SUBSCRIPTION,
        requested_model="claude-fable-5",
        request=request,
    )
    assert start.credential_plane == CredentialPlane.SUBSCRIPTION
    for changes in (
        {"provider": ProviderId.OPENAI},
        {"credential_plane": CredentialPlane.METERED_API},
        {"channel": ProviderChannel.ANTHROPIC_API},
    ):
        with pytest.raises(ValidationError):
            start.model_copy(update=changes, deep=True).__class__.model_validate(
                {**start.model_dump(), **changes}
            )
    assert all("management" not in channel.value for channel in ProviderChannel)

    with pytest.raises(ValidationError, match="provider-reported or unavailable"):
        ProviderReceipt(
            request_id=request.request_id,
            supervision=request.supervision,
            provider=ProviderId.ANTHROPIC,
            channel=ProviderChannel.CLAUDE_CLI,
            credential_plane=CredentialPlane.SUBSCRIPTION,
            route=request.route,
            requested_model="claude-fable-5",
            resolved_model="claude-fable-5",
            model_source="requested_fallback",
            endpoint_host="local-process",
            endpoint_kind="local_cli",
            credential_kind="host_oauth",
            billing_class="subscription",
            cost_usd=Decimal("0.001"),
            cost_source="locally_computed",
            region="host_subscription",
            duration_ms=1,
            usage=ProviderTokenUsage(),
        )


def test_claude_environment_strips_every_api_and_cloud_fallback_credential():
    secrets = {
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "CLAUDE_API_KEY": "claude-secret",
        "OPENAI_API_KEY": "openai-secret",
        "GEMINI_API_KEY": "gemini-secret",
        "GOOGLE_API_KEY": "google-secret",
        "XAI_API_KEY": "xai-secret",
        "GROK_API_KEY": "grok-secret",
        "XAI_MANAGEMENT_API_KEY": "management-secret",
        "UNIGROK_API_KEYS": "gateway-client-secret",
        "GOOGLE_APPLICATION_CREDENTIALS": "/private/adc.json",
        "AWS_ACCESS_KEY_ID": "aws-id",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "AWS_SESSION_TOKEN": "aws-token",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1",
    }
    clean = _claude_subscription_environment(
        {
            **secrets,
            "HOME": "/Users/example",
            "PATH": "/usr/bin",
            "CLAUDE_CODE_OAUTH_TOKEN": "host-oauth-token",
            "GITHUB_TOKEN": "unrelated-secret",
            "DATABASE_URL": "postgresql://private",
        }
    )
    assert clean["HOME"] == "/Users/example"
    assert clean["PATH"] == "/usr/bin"
    assert clean["CLAUDE_CODE_OAUTH_TOKEN"] == "host-oauth-token"
    assert clean["CLAUDE_CODE_SAFE_MODE"] == "1"
    assert clean["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert clean["DISABLE_AUTOUPDATER"] == "1"
    assert clean["DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert clean["DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert clean["DISABLE_TELEMETRY"] == "1"
    assert "GITHUB_TOKEN" not in clean
    assert "DATABASE_URL" not in clean
    assert not set(secrets).intersection(clean)
    rendered = json.dumps(clean)
    assert all(value not in rendered for value in secrets.values() if len(value) > 2)


@pytest.mark.asyncio
async def test_claude_cli_is_stdin_only_safe_mode_no_tools_and_subscription_honest():
    runner = CapturingRunner()
    adapter = ClaudeCLIAdapter(
        executable="/opt/local/bin/claude",
        environ={
            "HOME": "/Users/example",
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "must-not-reach-child",
        },
        runner=runner,
    )
    response = await adapter.complete(_request(model="claude-fable-5"))
    assert len(runner.calls) == 1
    call = runner.calls[0]
    argv = call["argv"]
    assert isinstance(argv, tuple)
    assert argv[0] == "/opt/local/bin/claude"
    assert "--safe-mode" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "subordinate semantic worker" in argv[argv.index("--system-prompt") + 1]
    assert "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv
    assert "--no-session-persistence" in argv
    assert "--no-chrome" in argv
    assert "--fallback-model" not in argv
    assert "--resume" not in argv
    assert "--continue" not in argv
    assert "--plugin-dir" not in argv
    assert "ANTHROPIC_API_KEY" not in call["env"]
    prompt = json.loads(call["stdin_bytes"])
    assert prompt["role"] == "subordinate_worker"
    assert prompt["authority"]["may_finalize"] is False
    assert "Supervisor TTL expires" in prompt["messages"][0]["content"]
    assert response.provider == ProviderId.ANTHROPIC
    assert response.channel == ProviderChannel.CLAUDE_CLI
    assert response.receipt.credential_plane == CredentialPlane.SUBSCRIPTION
    assert response.receipt.billing_class == "subscription"
    assert response.receipt.endpoint_kind == "local_cli"
    assert response.receipt.cost_usd is None
    assert response.receipt.cost_source == "unavailable"
    assert response.receipt.usage.total_tokens == 11
    assert response.receipt.response_id == "00000000-0000-4000-8000-000000000002"
    assert response.receipt.authority.may_finalize is False


@pytest.mark.asyncio
async def test_claude_timeout_and_ttl_are_bounded_before_any_process_effect():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    runner = CapturingRunner()
    adapter = ClaudeCLIAdapter(runner=runner, environ={}, clock=lambda: now)
    await adapter.complete(
        _request(
            now=now,
            ttl_seconds=2.5,
            timeout_seconds=30.0,
        )
    )
    assert runner.calls[0]["timeout_seconds"] == pytest.approx(2.5)

    expired = _request(now=now, ttl_seconds=-1)
    result = await adapter.attempt(expired)
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "ttl_expired"
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_direct_process_runner_has_real_timeout_kill_and_output_caps(tmp_path):
    with pytest.raises(_ProcessTimeout):
        await _run_bounded_process(
            argv=(sys.executable, "-c", "import time; time.sleep(5)"),
            stdin_bytes=b"",
            cwd=str(tmp_path),
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=0.05,
            stdout_limit_bytes=100,
            stderr_limit_bytes=100,
        )
    with pytest.raises(_ProcessOutputLimit):
        await _run_bounded_process(
            argv=(sys.executable, "-c", "print('x' * 1000)"),
            stdin_bytes=b"",
            cwd=str(tmp_path),
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=2.0,
            stdout_limit_bytes=32,
            stderr_limit_bytes=32,
        )


@pytest.mark.asyncio
async def test_process_runner_uses_direct_argv_without_a_shell(monkeypatch, tmp_path):
    observed = {}

    class FakeStdin:
        def __init__(self):
            self.content = bytearray()

        def write(self, content):
            self.content.extend(content)

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"bounded stdout")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.killed = False

        async def wait(self):
            return 0

        def kill(self):
            self.killed = True

    process = FakeProcess()

    async def fake_create_subprocess_exec(*argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    result = await _run_bounded_process(
        argv=("/absolute/claude", "--print", "literal;$()"),
        stdin_bytes=b"stdin prompt",
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin"},
        timeout_seconds=1.0,
        stdout_limit_bytes=128,
        stderr_limit_bytes=128,
    )
    assert observed["argv"] == ("/absolute/claude", "--print", "literal;$()")
    assert "shell" not in observed["kwargs"]
    assert process.stdin.content == b"stdin prompt"
    assert result.stdout == b"bounded stdout"


def test_claude_executable_rejects_relative_path_aliases():
    with pytest.raises(ProviderConfigurationError, match="invalid_cli_executable"):
        ClaudeCLIAdapter(executable="relative/path/claude", environ={})


@pytest.mark.asyncio
async def test_process_runner_kills_child_when_timeout_fires(monkeypatch, tmp_path):
    class FakeStdin:
        def write(self, _content):
            return None

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    class SlowProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.done = asyncio.Event()
            self.killed = False

        async def wait(self):
            await self.done.wait()
            return -9

        def kill(self):
            self.killed = True
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.done.set()

    process = SlowProcess()

    async def fake_create_subprocess_exec(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    with pytest.raises(_ProcessTimeout):
        await _run_bounded_process(
            argv=("/absolute/claude",),
            stdin_bytes=b"prompt",
            cwd=str(tmp_path),
            env={},
            timeout_seconds=0.01,
            stdout_limit_bytes=128,
            stderr_limit_bytes=128,
        )
    assert process.killed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "error_code"),
    [
        (b"not-json", "invalid_cli_json"),
        (b"[]", "invalid_cli_json"),
        (
            _claude_result().replace(
                b'"is_error": false',
                b'"is_error": true, "is_error": false',
                1,
            ),
            "invalid_cli_json",
        ),
        (_claude_result(extra={"unexpected": "field"}), "invalid_cli_json"),
        (_claude_result(extra={"num_turns": 2}), "invalid_cli_json"),
        (_claude_result(extra={"total_cost_usd": float("inf")}), "invalid_cli_json"),
        (_claude_result(extra={"stop_reason": "tool_use"}), "invalid_cli_json"),
        (
            _claude_result(
                extra={
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "server_tool_use": {"web_search_requests": 1},
                    }
                }
            ),
            "invalid_cli_json",
        ),
        (_claude_result(model="gpt-5.1"), "invalid_cli_json"),
        (
            _claude_result(
                extra={
                    "modelUsage": {
                        "claude-a": {"inputTokens": 1, "outputTokens": 1},
                        "claude-b": {"inputTokens": 1, "outputTokens": 1},
                    }
                }
            ),
            "ambiguous_reported_model",
        ),
    ],
)
async def test_claude_json_wrapper_fails_closed(stdout, error_code):
    adapter = ClaudeCLIAdapter(
        environ={},
        runner=CapturingRunner(
            CLIProcessResult(returncode=0, stdout=stdout, stderr=b"", duration_ms=1)
        ),
    )
    result = await adapter.attempt(_request())
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == error_code
    assert result.failure.billing_class == "subscription"
    assert result.failure.usage.source == "unavailable"
    assert result.failure.cost_usd is None


@pytest.mark.asyncio
async def test_cli_return_code_and_injected_runner_errors_are_secret_safe():
    secret = "private-provider-error-body"

    class BrokenRunner:
        async def __call__(self, **_kwargs):
            raise RuntimeError(secret)

    failed = await ClaudeCLIAdapter(environ={}, runner=BrokenRunner()).attempt(
        _request()
    )
    assert failed.failure is not None
    assert failed.failure.error_code == "process_failed"
    assert secret not in failed.failure.model_dump_json()

    nonzero = await ClaudeCLIAdapter(
        environ={},
        runner=CapturingRunner(
            CLIProcessResult(
                returncode=1,
                stdout=b"",
                stderr=secret.encode(),
                duration_ms=1,
            )
        ),
    ).attempt(_request())
    assert nonzero.failure is not None
    assert nonzero.failure.error_code == "process_failed"
    assert secret not in nonzero.failure.model_dump_json()


@pytest.mark.asyncio
async def test_cli_adapter_rechecks_fake_runner_output_caps():
    oversized = CLIProcessResult(
        returncode=0,
        stdout=b"x" * (MAX_CLI_STDOUT_BYTES + 1),
        stderr=b"",
        duration_ms=1,
    )
    result = await ClaudeCLIAdapter(
        environ={}, runner=CapturingRunner(oversized)
    ).attempt(_request())
    assert result.failure is not None
    assert result.failure.error_code == "response_too_large"
    assert MAX_CLI_STDERR_BYTES < MAX_CLI_STDOUT_BYTES


@pytest.mark.asyncio
async def test_sampling_requires_advertised_capability_and_never_calls_when_absent():
    calls = 0

    async def callback(_request):
        nonlocal calls
        calls += 1
        raise AssertionError("capability-absent client was called")

    request = _request(model="gemini-3.5-flash")
    with pytest.raises(ValueError, match="sealed binding"):
        _sampling_binding(
            callback,
            request=request,
            provider=ProviderId.GOOGLE,
            client_id="antigravity",
            sampling=False,
        )
    assert calls == 0


def test_sampling_binding_rejects_bare_callbacks_and_cross_provider_reuse():
    async def callback(_request):
        raise AssertionError("construction must stay inert")

    request = _request(model="gpt-5.1")
    models = ProviderModelPins(
        planning="gpt-5.1",
        coding="gpt-5.1",
        vision="gpt-5.1",
        research="gpt-5.1",
    )
    digest = transport_resource_identity("test", "bare-callback")
    descriptor = ProviderDescriptor(
        provider=ProviderId.OPENAI,
        channel=ProviderChannel.OPENAI_MCP_SAMPLING,
        credential_plane=CredentialPlane.SUBSCRIPTION,
        display_name="openai IDE subscription",
        endpoint_host="mcp-client",
        endpoint_kind="mcp_client_sampling",
        credential_kind="mcp_client_subscription",
        billing_class="subscription",
        client_identity="mcp-" + digest.removeprefix("sha256:"),
        transport_resource_identity=transport_resource_identity(
            "mcp_sampling_capability", digest
        ),
        credential_env_names=(),
        credential_state=CredentialState.DEFERRED,
        models=models,
        supported_routes=(request.route,),
        max_output_tokens=32_768,
        max_timeout_seconds=120.0,
        data_handling="provider_managed",
        residency="client_subscription",
    )
    with pytest.raises(TypeError, match="stateful MCP sampling factory"):
        SamplingClientBinding(
            capability=SamplingCapability(
                client_id="mcp-" + digest.removeprefix("sha256:"),
                sampling=True,
            ),
            callback=callback,
            provider=ProviderId.OPENAI,
            channel=ProviderChannel.OPENAI_MCP_SAMPLING,
            models=models,
            descriptor=descriptor,
            binding_digest=digest,
            supervision=request.supervision,
            provider_request_id=request.request_id,
            provider_request_digest=provider_request_digest(request),
            route=request.route,
            effect_claimed=lambda: False,
        )

    binding = _sampling_binding(callback, request=request)
    with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
        _create_sealed_mcp_sampling_adapter(
            provider=ProviderId.ANTHROPIC,
            channel=ProviderChannel.ANTHROPIC_MCP_SAMPLING,
            binding=binding,
        )
    with pytest.raises(TypeError, match="stateful MCP sampling lease"):
        MCPClientSamplingAdapter(
            provider=ProviderId.OPENAI,
            channel=ProviderChannel.OPENAI_MCP_SAMPLING,
            binding=binding,
        )

    google_request = _request(model="gemini-3.5-flash")
    relabeled = _sampling_binding(
        callback,
        request=google_request,
        provider=ProviderId.GOOGLE,
    )
    object.__setattr__(relabeled, "provider", ProviderId.OPENAI)
    object.__setattr__(relabeled, "channel", ProviderChannel.OPENAI_MCP_SAMPLING)
    with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
        _create_sealed_mcp_sampling_adapter(
            provider=ProviderId.OPENAI,
            channel=ProviderChannel.OPENAI_MCP_SAMPLING,
            binding=relabeled,
        )

    wrong_identity = _sampling_binding(callback, request=request)
    object.__setattr__(wrong_identity.capability, "client_id", "mcp-" + ("0" * 64))
    with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
        _create_sealed_mcp_sampling_adapter(
            provider=ProviderId.OPENAI,
            channel=ProviderChannel.OPENAI_MCP_SAMPLING,
            binding=wrong_identity,
        )


@pytest.mark.asyncio
async def test_sampling_grant_matches_exact_supervision_request_route_and_model_pre_effect():
    calls = 0

    async def callback(_request):
        nonlocal calls
        calls += 1
        return ClientSamplingResult(
            content=SamplingTextContent(type="text", text="unreachable"),
            model="gpt-5.1",
            stop_reason="endTurn",
        )

    request = _request(model="gpt-5.1")
    adapter = _sampling_adapter(callback, request=request)
    other_supervision = request.supervision.model_copy(
        update={"objective_id": "objective-2"}
    )
    altered = (
        request.model_copy(update={"supervision": other_supervision}),
        request.model_copy(update={"request_id": "provider-request-2"}),
        request.model_copy(update={"route": RouteClass.CODING}),
        request.model_copy(update={"model": "gpt-5.2"}),
        request.model_copy(
            update={
                "messages": (ProviderMessage(role="user", content="Changed content."),)
            }
        ),
        request.model_copy(update={"max_output_tokens": 2048}),
        request.model_copy(update={"timeout_seconds": 12.0}),
        request.model_copy(update={"temperature": 0.8}),
    )
    for candidate in altered:
        result = await adapter.attempt(candidate)
        assert result.failure is not None
        assert result.failure.error_code == "sampling_grant_mismatch"
    assert calls == 0


@pytest.mark.asyncio
async def test_constructed_client_sampling_result_is_revalidated():
    async def callback(_request):
        return ClientSamplingResult.model_construct(
            role="assistant",
            content={"type": "tool_use", "text": "forbidden"},
            model="gpt-5.1",
            stop_reason="endTurn",
        )

    request = _request(model="gpt-5.1")
    result = await _sampling_adapter(callback, request=request).attempt(request)
    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"


@pytest.mark.asyncio
async def test_sampling_request_has_no_context_tools_or_implicit_model_and_receipts_identity():
    observed = []

    async def callback(request):
        observed.append(request)
        return ClientSamplingResult(
            content=SamplingTextContent(type="text", text="IDE subscription answer."),
            model="gpt-5.1",
            stop_reason="endTurn",
            response_id="sampling-response-1",
        )

    provider_request = _request(model="gpt-5.1", max_output_tokens=9000)
    binding = _sampling_binding(callback, request=provider_request)
    adapter = _create_sealed_mcp_sampling_adapter(
        provider=ProviderId.OPENAI,
        channel=ProviderChannel.OPENAI_MCP_SAMPLING,
        binding=binding,
    )
    assert adapter.descriptor.credential_state == CredentialState.DEFERRED
    response = await adapter.complete(provider_request)
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "sampling/createMessage"
    assert request.include_context == "none"
    assert request.max_tokens == 9000
    assert request.model_preferences.hints[0].name == "gpt-5.1"
    assert "Supervisor TTL expires" in request.system_prompt
    rendered_request = request.model_dump()
    assert "tools" not in rendered_request
    assert "tool_choice" not in rendered_request
    wire_request = request.model_dump(mode="json", by_alias=True)
    assert wire_request["includeContext"] == "none"
    assert wire_request["maxTokens"] == 9000
    assert wire_request["modelPreferences"]["hints"] == [{"name": "gpt-5.1"}]
    assert wire_request["messages"][0]["content"]["type"] == "text"
    assert response.receipt.provider == ProviderId.OPENAI
    assert response.receipt.channel == ProviderChannel.OPENAI_MCP_SAMPLING
    assert response.receipt.client_identity == binding.capability.client_id
    assert response.receipt.resolved_model == "gpt-5.1"
    assert response.receipt.response_id is None
    assert response.receipt.model_source == "requested_fallback"
    assert response.receipt.billing_class == "subscription"
    assert response.receipt.usage.source == "unavailable"
    assert response.receipt.cost_usd is None
    assert response.receipt.cost_source == "unavailable"
    assert response.authority.may_route is False
    assert response.authority.may_finalize is False


@pytest.mark.asyncio
async def test_sampling_rejects_provider_spoofing_model_spoofing_and_malformed_content():
    request = _request(model="gpt-5.1")

    async def wrong_model(_request):
        return {
            "content": {"type": "text", "text": "spoofed"},
            "model": "claude-fable-5",
            "role": "assistant",
            "stopReason": "endTurn",
        }

    adapter = _sampling_adapter(wrong_model, request=request)
    result = await adapter.attempt(request)
    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"

    async def spoofed_provider(_request):
        return {
            "provider": "openai",
            "content": {"type": "text", "text": "extra provider claim"},
            "model": "gpt-5.1",
            "stop_reason": "endTurn",
        }

    adapter = _sampling_adapter(spoofed_provider, request=request)
    result = await adapter.attempt(request)
    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"

    async def tool_content(_request):
        return {
            "content": {"type": "tool_use", "text": "forbidden"},
            "model": "gpt-5.1",
            "role": "assistant",
            "stopReason": "endTurn",
        }

    adapter = _sampling_adapter(tool_content, request=request)
    result = await adapter.attempt(request)
    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"

    with pytest.raises(ProviderConfigurationError, match="sampling_channel_mismatch"):
        _create_sealed_mcp_sampling_adapter(
            provider=ProviderId.ANTHROPIC,
            channel=ProviderChannel.OPENAI_MCP_SAMPLING,
            binding=_sampling_binding(spoofed_provider, request=request),
        )


@pytest.mark.asyncio
async def test_sampling_callback_errors_are_secret_safe():
    secret = "private-client-sampling-error"

    async def callback(_request):
        raise RuntimeError(secret)

    request = _request(model="gemini-3.5-flash")
    result = await _sampling_adapter(
        callback,
        request=request,
        provider=ProviderId.GOOGLE,
        client_id="antigravity",
    ).attempt(request)
    assert result.failure is not None
    assert result.failure.error_code == "sampling_failed"
    assert secret not in result.failure.model_dump_json()


@pytest.mark.asyncio
async def test_sampling_ttl_expiry_and_callback_timeout_are_bounded():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    calls = 0

    async def callback(_request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    expired_request = _request(now=now, ttl_seconds=-1, model="gemini-3.5-flash")
    adapter = _sampling_adapter(
        callback,
        request=expired_request,
        provider=ProviderId.GOOGLE,
        client_id="antigravity",
        clock=lambda: now,
    )
    expired = await adapter.attempt(expired_request)
    assert expired.failure is not None
    assert expired.failure.error_code == "ttl_expired"
    assert calls == 0

    # The absolute completion guard is independently tested with a real clock.
    timed_request = _request(ttl_seconds=0.02, model="gemini-3.5-flash")
    timed = _sampling_adapter(
        callback,
        request=timed_request,
        provider=ProviderId.GOOGLE,
        client_id="antigravity",
    )
    timeout = await timed.attempt(timed_request)
    assert timeout.failure is not None
    assert timeout.failure.error_code == "ttl_expired"
    assert calls == 1


@pytest.mark.asyncio
async def test_sampling_base_ttl_boundary_after_claim_is_internal():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    current = now
    claimed = False
    request = _request(
        now=now,
        ttl_seconds=10.0,
        model="gemini-3.5-flash",
    )

    async def callback(_request):
        nonlocal claimed, current
        claimed = True
        current = request.supervision.ttl_expires_at
        return ClientSamplingResult(
            content=SamplingTextContent(type="text", text="boundary result"),
            model="gemini-3.5-flash",
            stop_reason="endTurn",
        )

    result = await _sampling_adapter(
        callback,
        request=request,
        provider=ProviderId.GOOGLE,
        effect_claimed=lambda: claimed,
        clock=lambda: current,
    ).attempt(request)
    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"


@pytest.mark.asyncio
async def test_sampling_client_usage_cost_and_response_id_are_not_receipt_authority():
    async def callback(_request):
        return ClientSamplingResult(
            content=SamplingTextContent(type="text", text="Measured answer."),
            model="claude-fable-5",
            stop_reason="maxTokens",
            usage=ProviderTokenUsage(
                input_tokens=10,
                output_tokens=4,
                total_tokens=14,
                source="provider_exact",
            ),
            cost_usd=Decimal("0.00120000"),
            response_id="client-claimed-response",
        )

    request = _request(model="claude-fable-5")
    response = await _sampling_adapter(
        callback,
        request=request,
        provider=ProviderId.ANTHROPIC,
        client_id="claude-code",
    ).complete(request)
    assert response.finish_reason == "length"
    assert response.receipt.usage.total_tokens is None
    assert response.receipt.usage.source == "unavailable"
    assert response.receipt.cost_usd is None
    assert response.receipt.cost_source == "unavailable"
    assert response.receipt.response_id is None


def test_subscription_registry_is_inert_and_sampling_requires_live_lease():
    effects = 0

    async def runner(**_kwargs):
        nonlocal effects
        effects += 1
        raise AssertionError("registry construction executed Claude")

    registry = build_subscription_registry(
        environ={"OPENAI_API_KEY": "must-not-be-read"},
        claude_runner=runner,
    )
    assert set(registry) == {ProviderChannel.CLAUDE_CLI}
    assert effects == 0
    assert all(
        adapter.descriptor.credential_plane == CredentialPlane.SUBSCRIPTION
        for adapter in registry.values()
    )
    assert all(
        adapter.descriptor.billing_class == "subscription"
        for adapter in registry.values()
    )
    assert not any("codex" in channel.value for channel in registry)
    assert DISABLED_SUBSCRIPTION_SURFACES == {"codex_cli", "antigravity_token"}
    assert "antigravity" not in " ".join(
        descriptor.credential_kind
        for descriptor in (adapter.descriptor for adapter in registry.values())
    )
    assert effects == 0


def test_subscription_lane_identities_are_pinned_without_exposing_paths():
    environ = {"PATH": "/trusted/bin", "HOME": "/trusted/home"}
    unresolved = ClaudeCLIAdapter(environ=environ)
    assert unresolved.descriptor.transport_resource_identity is None
    assert unresolved._child_env["PATH"] == "/trusted/bin"
    environ["PATH"] = "/tmp/attacker/bin"
    assert unresolved._child_env["PATH"] == "/trusted/bin"
    with pytest.raises(ValueError, match="pinned transport resource"):
        GrokWorkerLaneAuthorization.from_descriptor(unresolved.descriptor)

    trusted = ClaudeCLIAdapter(executable="/trusted/bin/claude", environ={})
    attacker = ClaudeCLIAdapter(executable="/tmp/attacker/claude", environ={})
    assert trusted.descriptor.transport_resource_identity is not None
    assert attacker.descriptor.transport_resource_identity is not None
    assert trusted.descriptor.transport_resource_identity != (
        attacker.descriptor.transport_resource_identity
    )
    assert (
        GrokWorkerLaneAuthorization.from_descriptor(trusted.descriptor).contract_digest
        != GrokWorkerLaneAuthorization.from_descriptor(
            attacker.descriptor
        ).contract_digest
    )
    rendered = (
        trusted.descriptor.model_dump_json() + attacker.descriptor.model_dump_json()
    )
    assert "/trusted/bin/claude" not in rendered
    assert "/tmp/attacker/claude" not in rendered

    async def callback(_request):
        raise AssertionError("lane construction must remain inert")

    sampling = (
        (
            ProviderId.OPENAI,
            ProviderChannel.OPENAI_MCP_SAMPLING,
            "codex-client",
        ),
        (
            ProviderId.ANTHROPIC,
            ProviderChannel.ANTHROPIC_MCP_SAMPLING,
            "claude-client",
        ),
        (
            ProviderId.GOOGLE,
            ProviderChannel.GOOGLE_MCP_SAMPLING,
            "antigravity-client",
        ),
    )
    for provider, channel, client_id in sampling:
        descriptor = _sampling_adapter(
            callback,
            request=_request(
                model={
                    ProviderId.OPENAI: "gpt-5.1",
                    ProviderId.ANTHROPIC: "claude-fable-5",
                    ProviderId.GOOGLE: "gemini-3.5-flash",
                }[provider]
            ),
            provider=provider,
            channel=channel,
            client_id=client_id,
        ).descriptor
        assert descriptor.transport_resource_identity is not None
        assert GrokWorkerLaneAuthorization.from_descriptor(descriptor).contract_digest
