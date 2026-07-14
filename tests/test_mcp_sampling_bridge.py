from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import mcp.types as mcp_types
import pytest
from pydantic import ValidationError
from starlette.requests import Request
from mcp.server.session import ServerSession

from src.providers import (
    MCP_PROVIDER_CAPABILITIES_SCOPE_KEY,
    MCP_PROVIDER_GRANTS_SCOPE_KEY,
    MCP_SESSION_AUTHORIZATION_SCOPE_KEY,
    MCP_SESSION_RUNTIME_SCOPE_KEY,
    MAX_MCP_SAMPLING_EFFECT_CLAIMS,
    GrokSupervisorBinding,
    GrokWorkerLaneAuthorization,
    MCPSamplingSessionRuntime,
    MCPSessionAuthorization,
    ProviderAuthorizationInvariantError,
    ProviderChannel,
    ProviderConfigurationError,
    ProviderId,
    ProviderMessage,
    ProviderModelPins,
    ProviderRequest,
    RouteClass,
    TrustedMCPProviderCapability,
    TrustedMCPProviderGrant,
    create_stateful_mcp_sampling_lease,
    provider_request_digest,
)


NOW = datetime(2035, 1, 1, tzinfo=UTC)
MODELS = ProviderModelPins(
    planning="gemini-3.5-pro",
    coding="gemini-3.5-code",
    vision="gemini-3.5-vision",
    research="gemini-3.5-research",
)


class FakeServerSession:
    def __init__(self, handler=None, *, sampling: bool = True) -> None:
        self.calls: list[dict] = []
        self._handler = handler
        self.client_params = SimpleNamespace(
            capabilities=SimpleNamespace(sampling=object() if sampling else None),
            clientInfo=SimpleNamespace(name="Antigravity"),
        )

    async def create_message(self, **kwargs):
        self.calls.append(kwargs)
        if self._handler is not None:
            result = self._handler(kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="Bound observation."),
            model=MODELS.planning,
            stopReason="endTurn",
        )


def _authorization(
    *,
    binding_id: str = "binding-1",
    mcp_session_id: str = "mcp-session-1",
    principal: str = "http:anon",
    client_label: str = "antigravity",
    mcp_client_name: str = "Antigravity",
    trust: str = "verified_local",
    issued_at: datetime = NOW - timedelta(seconds=5),
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> MCPSessionAuthorization:
    return MCPSessionAuthorization(
        binding_id=binding_id,
        mcp_session_id=mcp_session_id,
        principal=principal,
        client_label=client_label,
        mcp_client_name=mcp_client_name,
        trust=trust,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _supervision(*, ttl: datetime | None = None) -> GrokSupervisorBinding:
    return GrokSupervisorBinding(
        session_id="grok-session-1",
        objective_id="objective-1",
        route_decision_id="route-1",
        ttl_expires_at=ttl or NOW + timedelta(minutes=2),
    )


def _capability(
    authorization: MCPSessionAuthorization,
    *,
    provider: ProviderId = ProviderId.GOOGLE,
    channel: ProviderChannel = ProviderChannel.GOOGLE_MCP_SAMPLING,
    models: ProviderModelPins = MODELS,
    supported_routes: tuple[RouteClass, ...] = (
        RouteClass.PLANNING,
        RouteClass.CODING,
        RouteClass.RESEARCH,
    ),
) -> TrustedMCPProviderCapability:
    return TrustedMCPProviderCapability(
        session_authorization_digest=authorization.authorization_digest,
        provider=provider,
        channel=channel,
        models=models,
        supported_routes=supported_routes,
    )


def _provider_request(
    *,
    supervision: GrokSupervisorBinding | None = None,
    request_id: str = "provider-request-1",
    timeout_seconds: float = 60.0,
) -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        supervision=supervision or _supervision(),
        route=RouteClass.PLANNING,
        messages=(
            ProviderMessage(role="system", content="Return one bounded observation."),
            ProviderMessage(role="user", content="Compare these."),
        ),
        model=MODELS.planning,
        max_output_tokens=512,
        timeout_seconds=timeout_seconds,
        temperature=0.1,
    )


def _grant(
    authorization: MCPSessionAuthorization,
    *,
    capability: TrustedMCPProviderCapability | None = None,
    supervision: GrokSupervisorBinding | None = None,
    related_request_id: str | int = "tool-request-7",
    provider_request_id: str = "provider-request-1",
    timeout_seconds: float = 60.0,
    issued_at: datetime = NOW - timedelta(seconds=2),
    expires_at: datetime = NOW + timedelta(minutes=1),
) -> TrustedMCPProviderGrant:
    capability = capability or _capability(authorization)
    supervision = supervision or _supervision()
    provider_request = _provider_request(
        supervision=supervision,
        request_id=provider_request_id,
        timeout_seconds=timeout_seconds,
    )
    return TrustedMCPProviderGrant(
        grant_id="grant-1",
        session_authorization_digest=authorization.authorization_digest,
        session_capability_digest=capability.capability_digest,
        supervision=supervision,
        provider_request_id=provider_request_id,
        provider_request_digest=provider_request_digest(provider_request),
        mcp_related_request_id=related_request_id,
        provider=capability.provider,
        channel=capability.channel,
        route=RouteClass.PLANNING,
        models=capability.models,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _context(
    *,
    authorization: MCPSessionAuthorization | None = None,
    capability: TrustedMCPProviderCapability | None = None,
    grant: TrustedMCPProviderGrant | None = None,
    runtime: MCPSamplingSessionRuntime | None = None,
    session: FakeServerSession | None = None,
    request_id: str | int = "tool-request-7",
    stateless: bool = False,
):
    authorization = authorization or _authorization()
    capability = capability or _capability(authorization)
    grant = grant or _grant(
        authorization,
        capability=capability,
        related_request_id=request_id,
    )
    runtime = runtime or MCPSamplingSessionRuntime(authorization)
    session = session or FakeServerSession()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (b"mcp-session-id", authorization.mcp_session_id.encode()),
            (b"x-client-id", authorization.client_label.encode()),
        ],
        MCP_SESSION_AUTHORIZATION_SCOPE_KEY: authorization,
        MCP_PROVIDER_CAPABILITIES_SCOPE_KEY: (capability,),
        MCP_PROVIDER_GRANTS_SCOPE_KEY: (grant,),
        MCP_SESSION_RUNTIME_SCOPE_KEY: runtime,
    }
    request = Request(scope)
    request_context = SimpleNamespace(
        request=request,
        session=session,
        request_id=request_id,
    )
    ctx = SimpleNamespace(
        fastmcp=SimpleNamespace(settings=SimpleNamespace(stateless_http=stateless)),
        request_context=request_context,
        session=session,
    )
    return ctx, request, session, authorization, grant, runtime


def _request_for_grant(grant: TrustedMCPProviderGrant) -> ProviderRequest:
    return _provider_request(
        supervision=grant.supervision,
        request_id=grant.provider_request_id,
    )


def _lease(ctx, grant: TrustedMCPProviderGrant | None = None, **kwargs):
    if grant is None:
        grant = ctx.request_context.request.scope[MCP_PROVIDER_GRANTS_SCOPE_KEY][0]
    provider_request = kwargs.pop(
        "provider_request",
        _request_for_grant(grant),
    )
    clock = kwargs.pop("clock", lambda: NOW)
    return create_stateful_mcp_sampling_lease(
        ctx,
        provider=grant.provider,
        channel=grant.channel,
        provider_request=provider_request,
        clock=clock,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_exact_stateful_session_success_is_text_only_and_request_scoped():
    ctx, _, session, authorization, grant, _ = _context()
    lease = _lease(ctx, grant)
    provider_request = _request_for_grant(grant)

    async with lease:
        adapter = lease.adapter
        assert not hasattr(lease, "binding")
        result = await adapter.complete(provider_request)
        assert result.text == "Bound observation."
        assert result.model == MODELS.planning
        assert result.receipt.usage.source == "unavailable"
        assert result.receipt.cost_usd is None
        assert result.receipt.response_id is None
        assert result.receipt.supervision == grant.supervision
        assert result.receipt.request_id == grant.provider_request_id
        assert result.receipt.route == grant.route
        assert adapter.descriptor.client_identity.startswith("mcp-")
        rendered = (
            adapter.descriptor.client_identity
            + adapter.descriptor.transport_resource_identity
        )
        for raw in (
            authorization.principal,
            authorization.mcp_session_id,
            authorization.client_label,
            authorization.mcp_client_name,
        ):
            assert raw not in rendered

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["include_context"] == "none"
    assert call["tools"] is None
    assert call["related_request_id"] == grant.mcp_related_request_id
    assert call["messages"][0].content.type == "text"
    with pytest.raises(
        ProviderAuthorizationInvariantError,
        match="sampling_effect_indeterminate",
    ):
        await adapter.complete(provider_request)


@pytest.mark.asyncio
async def test_request_grants_share_stable_capability_descriptor_without_cross_authority():
    authorization = _authorization()
    capability = _capability(authorization)
    # This exact public lane identity exists before either request grant.
    pregrant_descriptor = capability.descriptor
    pregrant_lane = GrokWorkerLaneAuthorization.from_descriptor(pregrant_descriptor)
    runtime = MCPSamplingSessionRuntime(authorization)
    session = FakeServerSession()
    supervision = _supervision()
    grant1 = _grant(
        authorization,
        capability=capability,
        supervision=supervision,
        related_request_id="tool-request-1",
        provider_request_id="provider-request-1",
    )
    grant2 = TrustedMCPProviderGrant(
        **{
            **_grant(
                authorization,
                capability=capability,
                supervision=supervision,
                related_request_id="tool-request-2",
                provider_request_id="provider-request-2",
            ).model_dump(),
            "grant_id": "grant-2",
        }
    )
    request1 = _request_for_grant(grant1)
    request2 = _request_for_grant(grant2)
    ctx1, *_ = _context(
        authorization=authorization,
        capability=capability,
        grant=grant1,
        runtime=runtime,
        session=session,
        request_id="tool-request-1",
    )
    ctx2, *_ = _context(
        authorization=authorization,
        capability=capability,
        grant=grant2,
        runtime=runtime,
        session=session,
        request_id="tool-request-2",
    )

    async with _lease(ctx1, grant1) as lease1, _lease(ctx2, grant2) as lease2:
        assert lease1.adapter.descriptor == pregrant_descriptor
        assert lease2.adapter.descriptor == pregrant_descriptor
        assert (
            GrokWorkerLaneAuthorization.from_descriptor(
                lease1.adapter.descriptor
            ).contract_digest
            == pregrant_lane.contract_digest
            == GrokWorkerLaneAuthorization.from_descriptor(
                lease2.adapter.descriptor
            ).contract_digest
        )
        authority1 = object.__getattribute__(lease1.adapter, "_sampling_authority")
        authority2 = object.__getattribute__(lease2.adapter, "_sampling_authority")
        assert grant1.grant_digest != grant2.grant_digest
        assert grant1.provider_request_id != grant2.provider_request_id
        assert grant1.provider_request_digest != grant2.provider_request_digest
        assert grant1.effect_digest != grant2.effect_digest
        assert authority1.delegation_digest != authority2.delegation_digest
        rendered_descriptor = pregrant_descriptor.model_dump_json()
        for private_identity in (
            grant1.grant_digest,
            grant2.grant_digest,
            grant1.provider_request_id,
            grant2.provider_request_id,
            grant1.provider_request_digest,
            grant2.provider_request_digest,
            grant1.effect_digest,
            grant2.effect_digest,
            authority1.delegation_digest,
            authority2.delegation_digest,
        ):
            assert private_identity not in rendered_descriptor

        crossed1 = await lease1.adapter.attempt(request2)
        crossed2 = await lease2.adapter.attempt(request1)
        assert crossed1.failure is not None
        assert crossed1.failure.error_code == "sampling_grant_mismatch"
        assert crossed2.failure is not None
        assert crossed2.failure.error_code == "sampling_grant_mismatch"
        assert session.calls == []

        first = await lease1.adapter.attempt(request1)
        second = await lease2.adapter.attempt(request2)
        replay1 = await lease1.adapter.attempt(request1)
        replay2 = await lease2.adapter.attempt(request2)

    assert first.status == second.status == "returned"
    assert replay1.failure is not None
    assert replay1.failure.error_code == "sampling_effect_already_claimed"
    assert replay2.failure is not None
    assert replay2.failure.error_code == "sampling_effect_already_claimed"
    assert len(session.calls) == 2


def test_authenticated_session_client_and_provider_capability_change_descriptor_identity():
    base_authorization = _authorization()
    session_authorization = _authorization(
        binding_id="binding-2",
        mcp_session_id="mcp-session-2",
    )
    client_authorization = _authorization(
        binding_id="binding-3",
        mcp_session_id="mcp-session-3",
        client_label="codex",
        mcp_client_name="Codex Desktop",
    )
    google = _capability(base_authorization)
    other_session = _capability(session_authorization)
    other_client = _capability(client_authorization)
    openai_models = ProviderModelPins(
        planning="gpt-5.1",
        coding="gpt-5.1-code",
        vision="gpt-5.1-vision",
        research="gpt-5.1-research",
    )
    openai = _capability(
        base_authorization,
        provider=ProviderId.OPENAI,
        channel=ProviderChannel.OPENAI_MCP_SAMPLING,
        models=openai_models,
    )

    capabilities = (google, other_session, other_client, openai)
    assert len({item.capability_digest for item in capabilities}) == len(capabilities)
    descriptors = tuple(item.descriptor for item in capabilities)
    assert len({item.client_identity for item in descriptors}) == len(descriptors)
    assert len({item.transport_resource_identity for item in descriptors}) == len(
        descriptors
    )
    exposed_openai = openai.descriptor
    object.__setattr__(openai_models, "planning", "forged-input-model")
    object.__setattr__(exposed_openai.models, "planning", "forged-descriptor-model")
    assert openai.models.planning == "gpt-5.1"
    assert openai.descriptor.models.planning == "gpt-5.1"
    rendered = "".join(item.model_dump_json() for item in descriptors)
    for raw_identity in (
        base_authorization.principal,
        base_authorization.mcp_session_id,
        base_authorization.client_label,
        base_authorization.mcp_client_name,
        client_authorization.mcp_session_id,
        client_authorization.client_label,
        client_authorization.mcp_client_name,
    ):
        assert raw_identity not in rendered


@pytest.mark.asyncio
async def test_lease_owned_adapter_authority_rejects_callback_and_state_mutation():
    async def forged_callback(_request):
        return {
            "role": "assistant",
            "content": {"type": "text", "text": "forged"},
            "model": MODELS.planning,
            "stopReason": "endTurn",
        }

    ctx, _, session, _, grant, _ = _context()
    request = _request_for_grant(grant)
    async with _lease(ctx, grant) as lease:
        adapter = lease.adapter
        authority = object.__getattribute__(adapter, "_sampling_authority")
        with pytest.raises(AttributeError, match="cannot be mutated"):
            adapter._sampling_callback = forged_callback
        with pytest.raises(AttributeError, match="cannot be mutated"):
            adapter.provider = ProviderId.OPENAI
        with pytest.raises(AttributeError, match="cannot be mutated"):
            adapter._sampling_authority = replace(authority, callback=forged_callback)

        # The historical mutable attribute name is no longer consumed.  Even
        # object-level injection cannot redirect the sealed callback.
        object.__setattr__(adapter, "_sampling_callback", forged_callback)
        result = await adapter.complete(request)

    assert result.text == "Bound observation."
    assert len(session.calls) == 1

    ctx, _, session, _, grant, _ = _context()
    request = _request_for_grant(grant)
    async with _lease(ctx, grant) as lease:
        adapter = lease.adapter
        authority = object.__getattribute__(adapter, "_sampling_authority")
        object.__setattr__(
            adapter,
            "_sampling_authority",
            replace(authority, callback=forged_callback),
        )
        with pytest.raises(
            ProviderAuthorizationInvariantError,
            match="sampling_adapter_authority_mutated",
        ):
            await adapter.complete(request)
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["request_id", "supervision", "route", "messages", "ttl"],
)
async def test_inflight_provider_request_mutation_cannot_forge_effect_or_receipt(
    mutation,
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        await release.wait()
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="snapshotted"),
            model=MODELS.planning,
            stopReason="endTurn",
        )

    ctx, _, session, _, grant, _ = _context(session=FakeServerSession(handler))
    supervision = GrokSupervisorBinding.model_validate_json(
        grant.supervision.model_dump_json()
    )
    request = _provider_request(
        supervision=supervision,
        request_id=grant.provider_request_id,
    )
    expected = ProviderRequest.model_validate_json(request.model_dump_json())
    async with _lease(ctx, grant, provider_request=request) as lease:
        task = asyncio.create_task(lease.adapter.attempt(request))
        await started.wait()
        if mutation == "request_id":
            object.__setattr__(request, "request_id", "forged-request")
        elif mutation == "supervision":
            object.__setattr__(
                request,
                "supervision",
                request.supervision.model_copy(
                    update={"objective_id": "forged-objective"}
                ),
            )
        elif mutation == "route":
            object.__setattr__(request, "route", RouteClass.CODING)
        elif mutation == "messages":
            object.__setattr__(
                request,
                "messages",
                (ProviderMessage(role="user", content="forged payload"),),
            )
        else:
            object.__setattr__(
                request.supervision,
                "ttl_expires_at",
                NOW + timedelta(hours=4),
            )
        release.set()
        result = await task

    assert result.response is not None
    receipt = result.response.receipt
    assert receipt.request_id == expected.request_id
    assert receipt.supervision == expected.supervision
    assert receipt.route == expected.route
    assert result.response.text == "snapshotted"
    assert len(session.calls) == 1
    assert session.calls[0]["messages"][-1].content.text == "Compare these."


@pytest.mark.asyncio
async def test_exposed_descriptor_is_a_copy_and_cannot_forge_inflight_receipt():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        await release.wait()
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="descriptor safe"),
            model=MODELS.planning,
            stopReason="endTurn",
        )

    ctx, _, session, _, grant, _ = _context(session=FakeServerSession(handler))
    request = _request_for_grant(grant)
    async with _lease(ctx, grant) as lease:
        exposed = lease.adapter.descriptor
        expected_identity = exposed.client_identity
        task = asyncio.create_task(lease.adapter.attempt(request))
        await started.wait()
        object.__setattr__(exposed, "client_identity", "forged-client")
        release.set()
        result = await task

    assert result.response is not None
    assert result.response.receipt.client_identity == expected_identity
    assert result.response.receipt.client_identity != "forged-client"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_same_adapter_grant_is_one_shot_and_replay_is_internal():
    ctx, _, session, _, grant, _ = _context()
    request = _request_for_grant(grant)
    async with _lease(ctx, grant) as lease:
        first = await lease.adapter.attempt(request)
        replay = await lease.adapter.attempt(request)

    assert first.status == "returned"
    assert replay.failure is not None
    assert replay.failure.error_kind == "internal"
    assert replay.failure.error_code == "sampling_effect_already_claimed"
    assert replay.failure.error_kind not in {
        "configuration",
        "transport",
        "protocol",
    }
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_same_adapter_replay_executes_exactly_one_effect():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        await release.wait()
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="one effect"),
            model=MODELS.planning,
            stopReason="endTurn",
        )

    ctx, _, session, _, grant, _ = _context(session=FakeServerSession(handler))
    request = _request_for_grant(grant)
    async with _lease(ctx, grant) as lease:
        first_task = asyncio.create_task(lease.adapter.attempt(request))
        await started.wait()
        replay_task = asyncio.create_task(lease.adapter.attempt(request))
        await asyncio.sleep(0)
        assert len(session.calls) == 1
        release.set()
        first, replay = await asyncio.gather(first_task, replay_task)

    assert first.status == "returned"
    assert replay.failure is not None
    assert replay.failure.error_kind == "internal"
    assert replay.failure.error_code == "sampling_effect_already_claimed"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_reissued_grant_across_leases_cannot_replay_same_effect():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        await release.wait()
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="one effect"),
            model=MODELS.planning,
            stopReason="endTurn",
        )

    authorization = _authorization()
    runtime = MCPSamplingSessionRuntime(authorization)
    session = FakeServerSession(handler)
    grant1 = _grant(authorization)
    grant2 = TrustedMCPProviderGrant(**{**grant1.model_dump(), "grant_id": "grant-2"})
    assert grant1.grant_digest != grant2.grant_digest
    assert grant1.effect_digest == grant2.effect_digest
    ctx1, *_ = _context(
        authorization=authorization,
        grant=grant1,
        runtime=runtime,
        session=session,
    )
    ctx2, *_ = _context(
        authorization=authorization,
        grant=grant2,
        runtime=runtime,
        session=session,
    )
    request = _request_for_grant(grant1)
    async with _lease(ctx1, grant1) as lease1, _lease(ctx2, grant2) as lease2:
        first_task = asyncio.create_task(lease1.adapter.attempt(request))
        await started.wait()
        replay_task = asyncio.create_task(lease2.adapter.attempt(request))
        await asyncio.sleep(0)
        assert len(session.calls) == 1
        release.set()
        first, replay = await asyncio.gather(first_task, replay_task)

    assert first.status == "returned"
    assert replay.failure is not None
    assert replay.failure.error_kind == "internal"
    assert replay.failure.error_code == "sampling_effect_already_claimed"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_same_request_id_with_different_payload_digest_is_still_one_shot():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        await release.wait()
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="one effect"),
            model=MODELS.planning,
            stopReason="endTurn",
        )

    authorization = _authorization()
    runtime = MCPSamplingSessionRuntime(authorization)
    session = FakeServerSession(handler)
    grant1 = _grant(authorization)
    request1 = _request_for_grant(grant1)
    request2 = ProviderRequest.model_validate(
        {
            **request1.model_dump(),
            "messages": (
                ProviderMessage(
                    role="system", content="Return one bounded observation."
                ),
                ProviderMessage(role="user", content="Different payload."),
            ),
        }
    )
    grant2 = TrustedMCPProviderGrant(
        **{
            **grant1.model_dump(),
            "grant_id": "grant-2",
            "provider_request_digest": provider_request_digest(request2),
        }
    )
    assert grant1.provider_request_digest != grant2.provider_request_digest
    assert grant1.effect_digest == grant2.effect_digest
    ctx1, *_ = _context(
        authorization=authorization,
        grant=grant1,
        runtime=runtime,
        session=session,
    )
    ctx2, *_ = _context(
        authorization=authorization,
        grant=grant2,
        runtime=runtime,
        session=session,
    )
    async with (
        _lease(ctx1, grant1, provider_request=request1) as lease1,
        _lease(ctx2, grant2, provider_request=request2) as lease2,
    ):
        first_task = asyncio.create_task(lease1.adapter.attempt(request1))
        await started.wait()
        replay_task = asyncio.create_task(lease2.adapter.attempt(request2))
        release.set()
        first, replay = await asyncio.gather(first_task, replay_task)

    assert first.status == "returned"
    assert replay.failure is not None
    assert replay.failure.error_kind == "internal"
    assert replay.failure.error_code == "sampling_effect_already_claimed"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_stateless_global_uninitialized_and_capability_absent_fail_closed():
    ctx, *_ = _context(stateless=True)
    with pytest.raises(ProviderConfigurationError, match="stateful_sampling_required"):
        _lease(ctx)

    ctx, _, _, _, grant, _ = _context()
    ctx.request_context.request = SimpleNamespace(scope={})
    with pytest.raises(
        ProviderConfigurationError, match="sampling_context_unavailable"
    ):
        _lease(ctx, grant)

    session = FakeServerSession()
    session.client_params = None
    ctx, *_ = _context(session=session)
    with pytest.raises(
        ProviderConfigurationError, match="sampling_capability_unavailable"
    ):
        async with _lease(ctx):
            pass

    ctx, *_ = _context(session=FakeServerSession(sampling=False))
    with pytest.raises(
        ProviderConfigurationError, match="sampling_capability_unavailable"
    ):
        async with _lease(ctx):
            pass


def test_installed_mcp_sdk_supports_exact_sampling_bridge_keywords():
    parameters = inspect.signature(ServerSession.create_message).parameters
    for name in (
        "messages",
        "max_tokens",
        "system_prompt",
        "include_context",
        "temperature",
        "model_preferences",
        "tools",
        "related_request_id",
    ):
        assert name in parameters
    assert parameters["tools"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["related_request_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["tools"].default is None
    assert parameters["related_request_id"].default is None


@pytest.mark.asyncio
async def test_session_effect_claim_ceiling_revokes_without_evicting_claims():
    runtime = MCPSamplingSessionRuntime(_authorization())
    first_digest = "sha256:" + f"{0:064x}"
    for index in range(MAX_MCP_SAMPLING_EFFECT_CLAIMS):
        assert runtime.claim_effect("sha256:" + f"{index:064x}") is True

    assert runtime.effect_claimed(first_digest) is True
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        runtime.claim_effect("sha256:" + f"{MAX_MCP_SAMPLING_EFFECT_CLAIMS:064x}")
    assert runtime.revoked is True
    assert runtime.effect_claimed(first_digest) is True


@pytest.mark.asyncio
async def test_nan_drain_timeouts_fail_closed():
    runtime = MCPSamplingSessionRuntime(_authorization())
    with pytest.raises(ValueError, match="drain timeout"):
        await runtime.revoke(drain_timeout_seconds=float("nan"))

    ctx, *_ = _context()
    with pytest.raises(
        ProviderConfigurationError,
        match="sampling_drain_timeout_invalid",
    ):
        _lease(ctx, drain_timeout_seconds=float("nan"))


def test_anonymous_remote_and_non_grok_provider_grants_are_rejected():
    with pytest.raises(ValidationError, match="anonymous MCP sampling"):
        _authorization(trust="authenticated_remote")

    authorization = _authorization(
        principal="oauth:user-1", trust="authenticated_remote"
    )
    grant = _grant(authorization)
    with pytest.raises(ValidationError):
        TrustedMCPProviderGrant.model_validate(
            {**grant.model_dump(), "issuer": "client"}
        )


@pytest.mark.asyncio
async def test_client_labels_never_infer_provider_or_model_authority():
    authorization = _authorization()
    object.__setattr__(authorization, "client_label", "openai-looking-client")
    # Rebuild a valid authorization after demonstrating that the label itself
    # carries no provider semantics.
    authorization = MCPSessionAuthorization(
        **{
            **_authorization().model_dump(),
            "client_label": "openai-looking-client",
        }
    )
    grant = _grant(authorization)
    ctx, request, session, *_ = _context(authorization=authorization, grant=grant)
    request.scope["headers"] = [
        (b"mcp-session-id", b"mcp-session-1"),
        (b"x-client-id", b"openai-looking-client"),
    ]
    lease = _lease(ctx, grant)
    async with lease:
        with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
            await lease.adapter.complete(
                _request_for_grant(grant).model_copy(
                    update={"model": "gpt-9-client-claim"}
                )
            )
    assert session.calls == []


@pytest.mark.asyncio
async def test_grant_is_bound_to_authorization_supervision_and_mcp_request():
    ctx, _, session, authorization, grant, _ = _context()
    forged = grant.model_copy(
        update={"session_authorization_digest": "sha256:" + ("0" * 64)}
    )
    ctx.request_context.request.scope[MCP_PROVIDER_GRANTS_SCOPE_KEY] = (forged,)
    with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
        _lease(ctx, forged)

    ctx, *_ = _context()
    ctx.request_context.request_id = "other-tool-request"
    with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
        _lease(ctx)

    # The grant model itself rejects outliving Grok supervision.
    with pytest.raises(ValidationError, match="cannot outlive Grok supervision"):
        TrustedMCPProviderGrant.model_validate(
            {
                **grant.model_dump(),
                "expires_at": grant.supervision.ttl_expires_at + timedelta(seconds=1),
            }
        )
    assert session.calls == []


@pytest.mark.asyncio
async def test_future_or_expired_authority_fails_before_effect():
    future_auth = _authorization(
        issued_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    future_grant = _grant(
        future_auth,
        issued_at=NOW + timedelta(seconds=2),
        expires_at=NOW + timedelta(minutes=1),
    )
    ctx, _, session, *_ = _context(
        authorization=future_auth,
        grant=future_grant,
    )
    with pytest.raises(
        ProviderConfigurationError, match="sampling_authority_not_yet_valid"
    ):
        async with _lease(ctx, future_grant):
            pass
    assert session.calls == []

    ctx, _, session, _, grant, _ = _context()
    with pytest.raises(ProviderConfigurationError, match="ttl_expired"):
        async with create_stateful_mcp_sampling_lease(
            ctx,
            provider=grant.provider,
            channel=grant.channel,
            provider_request=_request_for_grant(grant),
            clock=lambda: grant.expires_at,
        ):
            pass
    assert session.calls == []


@pytest.mark.asyncio
async def test_auth_capability_and_grant_object_mutation_is_detected_pre_effect():
    ctx, _, session, authorization, grant, _ = _context()
    lease = _lease(ctx, grant)
    async with lease:
        object.__setattr__(authorization, "principal", "oauth:attacker")
        with pytest.raises(
            ProviderConfigurationError, match="sampling_authorization_mismatch"
        ):
            await lease.adapter.complete(_request_for_grant(grant))
    assert session.calls == []

    ctx, _, session, _, grant, _ = _context()
    capability = ctx.request_context.request.scope[MCP_PROVIDER_CAPABILITIES_SCOPE_KEY][
        0
    ]
    lease = _lease(ctx, grant)
    async with lease:
        object.__setattr__(
            capability,
            "supported_routes",
            (RouteClass.CODING,),
        )
        with pytest.raises(
            ProviderConfigurationError, match="sampling_capability_mismatch"
        ):
            await lease.adapter.complete(_request_for_grant(grant))
    assert session.calls == []

    ctx, _, session, _, grant, _ = _context()
    lease = _lease(ctx, grant)
    async with lease:
        object.__setattr__(grant, "provider_request_id", "forged-request")
        with pytest.raises(ProviderConfigurationError, match="sampling_grant_mismatch"):
            await lease.adapter.complete(
                _provider_request(request_id="provider-request-1")
            )
    assert session.calls == []


@pytest.mark.asyncio
async def test_exact_context_session_header_client_and_request_id_are_rechecked():
    ctx, request, session, _, grant, _ = _context()
    lease = _lease(ctx, grant)
    async with lease:
        ctx.request_context.session = FakeServerSession()
        with pytest.raises(
            ProviderConfigurationError, match="sampling_session_mismatch"
        ):
            await lease.adapter.complete(_request_for_grant(grant))
    assert session.calls == []

    ctx, request, session, _, grant, _ = _context()
    lease = _lease(ctx, grant)
    async with lease:
        request.scope["headers"] = [
            (b"mcp-session-id", b"other-session"),
            (b"x-client-id", b"antigravity"),
        ]
        with pytest.raises(
            ProviderConfigurationError, match="sampling_session_mismatch"
        ):
            await lease.adapter.complete(_request_for_grant(grant))
    assert session.calls == []


@pytest.mark.asyncio
async def test_two_distinct_request_leases_share_one_session_concurrency_slot():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    max_active = 0

    async def handler(_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            if len(session.calls) == 1:
                first_started.set()
                await release_first.wait()
            return mcp_types.CreateMessageResult(
                role="assistant",
                content=mcp_types.TextContent(type="text", text="serialized"),
                model=MODELS.planning,
                stopReason="endTurn",
            )
        finally:
            active -= 1

    authorization = _authorization()
    supervision = _supervision()
    runtime = MCPSamplingSessionRuntime(authorization)
    session = FakeServerSession(handler)
    grant1 = _grant(
        authorization,
        supervision=supervision,
        related_request_id="tool-request-1",
        provider_request_id="provider-request-1",
    )
    grant2 = TrustedMCPProviderGrant(
        **{
            **_grant(
                authorization,
                supervision=supervision,
                related_request_id="tool-request-2",
                provider_request_id="provider-request-2",
            ).model_dump(),
            "grant_id": "grant-2",
        }
    )
    ctx1, *_ = _context(
        authorization=authorization,
        grant=grant1,
        runtime=runtime,
        session=session,
        request_id="tool-request-1",
    )
    ctx2, *_ = _context(
        authorization=authorization,
        grant=grant2,
        runtime=runtime,
        session=session,
        request_id="tool-request-2",
    )
    async with _lease(ctx1, grant1) as lease1, _lease(ctx2, grant2) as lease2:
        task1 = asyncio.create_task(lease1.adapter.complete(_request_for_grant(grant1)))
        await first_started.wait()
        task2 = asyncio.create_task(lease2.adapter.complete(_request_for_grant(grant2)))
        await asyncio.sleep(0)
        assert len(session.calls) == 1
        release_first.set()
        await asyncio.gather(task1, task2)

    assert len(session.calls) == 2
    assert max_active == 1


@pytest.mark.asyncio
async def test_lease_and_session_revocation_cancel_and_drain_active_calls():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    ctx, _, _, _, grant, runtime = _context(session=FakeServerSession(handler))
    lease = _lease(ctx, grant)
    await lease.__aenter__()
    adapter = lease.adapter
    task = asyncio.create_task(adapter.complete(_request_for_grant(grant)))
    await started.wait()
    await lease.revoke()
    assert cancelled.is_set()
    with pytest.raises(
        ProviderAuthorizationInvariantError,
        match="sampling_effect_indeterminate",
    ):
        await task
    with pytest.raises(
        ProviderAuthorizationInvariantError,
        match="sampling_effect_indeterminate",
    ):
        await adapter.complete(_request_for_grant(grant))

    started = asyncio.Event()
    cancelled = asyncio.Event()
    ctx, _, _, _, grant, runtime = _context(session=FakeServerSession(handler))
    lease = _lease(ctx, grant)
    await lease.__aenter__()
    task = asyncio.create_task(lease.adapter.complete(_request_for_grant(grant)))
    await started.wait()
    await runtime.revoke()
    with pytest.raises(
        ProviderAuthorizationInvariantError,
        match="sampling_effect_indeterminate",
    ):
        await task
    with pytest.raises(
        ProviderAuthorizationInvariantError,
        match="sampling_effect_indeterminate",
    ):
        await lease.adapter.complete(_request_for_grant(grant))
    await lease.revoke()


@pytest.mark.asyncio
async def test_outer_task_cancellation_after_claim_is_internal_indeterminate():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    ctx, _, session, _, grant, _ = _context(session=FakeServerSession(handler))
    async with _lease(ctx, grant) as lease:
        task = asyncio.create_task(lease.adapter.complete(_request_for_grant(grant)))
        await started.wait()
        assert lease.adapter.effect_claimed() is True
        task.cancel()
        with pytest.raises(
            ProviderAuthorizationInvariantError,
            match="sampling_effect_indeterminate",
        ):
            await task

    assert cancelled.is_set()
    assert task.cancelled() is False
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_outer_task_cancellation_before_claim_preserves_cancellation():
    ctx, _, session, _, grant, runtime = _context()
    semaphore = runtime.sampling_slot()
    await semaphore.acquire()
    try:
        async with _lease(ctx, grant) as lease:
            task = asyncio.create_task(
                lease.adapter.complete(_request_for_grant(grant))
            )
            for _ in range(10):
                await asyncio.sleep(0)
                if task in runtime._active_tasks:
                    break
            assert task in runtime._active_tasks
            assert task.done() is False
            assert lease.adapter.effect_claimed() is False
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lease.adapter.effect_claimed() is False
    finally:
        semaphore.release()

    assert task.cancelled() is True
    assert session.calls == []


@pytest.mark.asyncio
async def test_disconnect_and_callback_errors_are_secret_safe():
    secret = "private-session-transport-token"

    async def handler(_kwargs):
        raise RuntimeError(secret)

    ctx, _, _, _, grant, _ = _context(session=FakeServerSession(handler))
    async with _lease(ctx, grant) as lease:
        with pytest.raises(ProviderAuthorizationInvariantError) as exc_info:
            await lease.adapter.complete(_request_for_grant(grant))
    assert exc_info.value.code == "sampling_effect_indeterminate"
    assert secret not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["timeout", "disconnect", "protocol", "late_result", "auth_changed"],
)
async def test_every_post_claim_failure_is_internal_and_not_fallback_eligible(case):
    current = NOW
    authorization = _authorization()
    grant = _grant(authorization)

    async def handler(_kwargs):
        nonlocal current
        if case == "timeout":
            raise TimeoutError
        if case == "disconnect":
            raise RuntimeError("private disconnect detail")
        if case == "protocol":
            return mcp_types.CreateMessageResult(
                role="user",
                content=mcp_types.TextContent(type="text", text="wrong role"),
                model=MODELS.planning,
                stopReason="endTurn",
            )
        if case == "late_result":
            current = grant.expires_at
        if case == "auth_changed":
            object.__setattr__(authorization, "principal", "oauth:changed")
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text="late or changed"),
            model=MODELS.planning,
            stopReason="endTurn",
        )

    session = FakeServerSession(handler)
    ctx, *_ = _context(
        authorization=authorization,
        grant=grant,
        session=session,
    )
    async with _lease(ctx, grant, clock=lambda: current) as lease:
        assert lease.adapter.effect_claimed() is False
        result = await lease.adapter.attempt(_request_for_grant(grant))
        assert lease.adapter.effect_claimed() is True

    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"
    assert result.failure.error_kind not in {
        "configuration",
        "transport",
        "protocol",
    }
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_real_adapter_timeout_after_dispatch_is_internal():
    cancelled = asyncio.Event()

    async def handler(_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    authorization = _authorization()
    supervision = _supervision()
    request = _provider_request(
        supervision=supervision,
        timeout_seconds=1.0,
    )
    grant = _grant(
        authorization,
        supervision=supervision,
        timeout_seconds=1.0,
    )
    session = FakeServerSession(handler)
    ctx, *_ = _context(
        authorization=authorization,
        grant=grant,
        session=session,
    )
    async with _lease(ctx, grant, provider_request=request) as lease:
        result = await lease.adapter.attempt(request)
        assert lease.adapter.effect_claimed() is True

    assert cancelled.is_set()
    assert len(session.calls) == 1
    assert result.failure is not None
    assert result.failure.error_kind == "internal"
    assert result.failure.error_code == "sampling_effect_indeterminate"


@pytest.mark.asyncio
async def test_preclaim_context_failure_remains_fallback_eligible_configuration():
    ctx, request_scope, session, _, grant, _ = _context()
    async with _lease(ctx, grant) as lease:
        request_scope.scope["headers"] = [
            (b"mcp-session-id", b"wrong-session"),
            (b"x-client-id", b"antigravity"),
        ]
        assert lease.adapter.effect_claimed() is False
        result = await lease.adapter.attempt(_request_for_grant(grant))
        assert lease.adapter.effect_claimed() is False

    assert result.failure is not None
    assert result.failure.error_kind == "configuration"
    assert result.failure.error_code == "sampling_session_mismatch"
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["image", "user", "tool", "model", "constructed"])
async def test_sdk_results_are_revalidated_and_strictly_text_only(case):
    def handler(_kwargs):
        if case == "image":
            return mcp_types.CreateMessageResult(
                role="assistant",
                content=mcp_types.ImageContent(
                    type="image", data="AA==", mimeType="image/png"
                ),
                model=MODELS.planning,
                stopReason="endTurn",
            )
        if case == "user":
            return mcp_types.CreateMessageResult(
                role="user",
                content=mcp_types.TextContent(type="text", text="wrong role"),
                model=MODELS.planning,
                stopReason="endTurn",
            )
        if case == "tool":
            return mcp_types.CreateMessageResult(
                role="assistant",
                content=mcp_types.TextContent(type="text", text="tool requested"),
                model=MODELS.planning,
                stopReason="toolUse",
            )
        if case == "model":
            return mcp_types.CreateMessageResult(
                role="assistant",
                content=mcp_types.TextContent(type="text", text="wrong model"),
                model=MODELS.coding,
                stopReason="endTurn",
            )
        return mcp_types.CreateMessageResult.model_construct(
            role="assistant",
            content={"type": "tool_use", "name": "forbidden"},
            model=MODELS.planning,
            stopReason="endTurn",
        )

    ctx, _, _, _, grant, _ = _context(session=FakeServerSession(handler))
    async with _lease(ctx, grant) as lease:
        with pytest.raises(ProviderAuthorizationInvariantError) as exc_info:
            await lease.adapter.complete(_request_for_grant(grant))
    assert exc_info.value.code == "sampling_effect_indeterminate"
