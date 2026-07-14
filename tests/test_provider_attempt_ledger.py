from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest
from pydantic import ValidationError

from src.providers import (
    CredentialPlane,
    GrokSupervisorBinding,
    ProviderAttemptResult,
    ProviderAttemptStart,
    ProviderChannel,
    ProviderFailureReceipt,
    ProviderId,
    ProviderMessage,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    RouteClass,
    model_visible_messages,
)
from src.utils import GrokSessionStore


def _binding(*, session: str = "session-1") -> GrokSupervisorBinding:
    return GrokSupervisorBinding(
        session_id=session,
        objective_id="objective-1",
        route_decision_id="route-1",
        ttl_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )


def _request(
    *,
    request_id: str = "request-1",
    model: str = "gpt-5.1",
    session: str = "session-1",
    content: str = "Return one bounded planning observation.",
) -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        supervision=_binding(session=session),
        route=RouteClass.PLANNING,
        messages=[ProviderMessage(role="user", content=content)],
        model=model,
    )


def _start(
    *,
    attempt_id: str = "attempt-1",
    delegation_id: str = "delegation-1",
    ordinal: int = 1,
    request: ProviderRequest | None = None,
    provider: ProviderId = ProviderId.OPENAI,
    channel: ProviderChannel = ProviderChannel.OPENAI_API,
    credential_plane: CredentialPlane = CredentialPlane.METERED_API,
    model: str = "gpt-5.1",
    supervisor_model: str = "grok-4.5",
) -> ProviderAttemptStart:
    return ProviderAttemptStart(
        attempt_id=attempt_id,
        delegation_id=delegation_id,
        attempt_ordinal=ordinal,
        supervisor_plane="CLI",
        supervisor_model=supervisor_model,
        provider=provider,
        channel=channel,
        credential_plane=credential_plane,
        requested_model=model,
        request=request or _request(model=model),
    )


def _returned(
    start: ProviderAttemptStart,
    *,
    text: str = "Worker observation only.",
    usage: ProviderTokenUsage | None = None,
    cost: Decimal | None = None,
) -> ProviderAttemptResult:
    cost_source = "locally_computed" if cost is not None else "unavailable"
    receipt = ProviderReceipt(
        request_id=start.request.request_id,
        supervision=start.request.supervision,
        provider=start.provider,
        channel=start.channel,
        credential_plane=start.credential_plane,
        route=start.request.route,
        requested_model=start.requested_model,
        resolved_model=start.requested_model,
        model_source="provider_reported",
        endpoint_host=(
            "api.openai.com"
            if start.provider == ProviderId.OPENAI
            else "aiplatform.googleapis.com"
        ),
        endpoint_kind=(
            "first_party_api"
            if start.channel != ProviderChannel.VERTEX_ADC
            else "vertex_ai"
        ),
        credential_kind=(
            "google_adc"
            if start.channel == ProviderChannel.VERTEX_ADC
            else "api_key"
        ),
        cost_usd=cost,
        cost_source=cost_source,
        region="global",
        duration_ms=12,
        usage=usage or ProviderTokenUsage(),
    )
    return ProviderAttemptResult(
        status="returned",
        response=ProviderResponse(
            provider=start.provider,
            channel=start.channel,
            model=start.requested_model,
            text=text,
            finish_reason="stop",
            receipt=receipt,
        ),
    )


def _failed(start: ProviderAttemptStart, code: str = "provider_unavailable"):
    return ProviderAttemptResult(
        status="failed",
        failure=ProviderFailureReceipt(
            request_id=start.request.request_id,
            supervision=start.request.supervision,
            provider=start.provider,
            channel=start.channel,
            credential_plane=start.credential_plane,
            route=start.request.route,
            requested_model=start.requested_model,
            endpoint_host="generativelanguage.googleapis.com",
            error_kind="transport",
            error_code=code,
            duration_ms=8,
        ),
    )


def test_attempt_start_is_grok_bound_and_channel_consistent():
    start = _start()
    messages = model_visible_messages(start.request)
    assert messages[0].role == "system"
    assert "2030-01-01T00:00:00Z" in messages[0].content
    assert messages[-1].role == "user"

    with pytest.raises(ValidationError, match="provider and physical channel"):
        _start(channel=ProviderChannel.ANTHROPIC_API)
    with pytest.raises(ValidationError, match="credential plane"):
        _start(credential_plane=CredentialPlane.SUBSCRIPTION)
    with pytest.raises(ValidationError, match="exact Grok supervisor"):
        _start(supervisor_model="claude-fable-5")
    with pytest.raises(ValidationError, match="supervisor attempts"):
        _start(
            provider=ProviderId.XAI,
            channel=ProviderChannel.XAI_API,
            model="grok-4.5",
            request=_request(model="grok-4.5"),
        )


@pytest.mark.asyncio
async def test_begin_and_complete_are_idempotent_transport_evidence(tmp_path):
    store = GrokSessionStore(tmp_path / "ledger.db")
    start = _start()
    usage = ProviderTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider_exact",
    )
    result = _returned(start, usage=usage, cost=Decimal("0.00125000"))

    assert await store.begin_provider_attempt(start) is True
    assert await store.begin_provider_attempt(start) is False
    assert await store.complete_provider_attempt(start.attempt_id, result) is True
    assert await store.complete_provider_attempt(start.attempt_id, result) is False

    rows = await store.list_provider_attempts(delegation_id=start.delegation_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["supervisor"] == "grok"
    assert row["transport_status"] == "returned"
    assert row["harvest_status"] == "pending"
    assert row["usage_source"] == "provider_exact"
    assert row["cost_usd"] == "0.00125000"
    assert row["cost_source"] == "locally_computed"
    assert "Supervisor TTL expires" in row["prompt_text"]
    assert row["output_text"] == "Worker observation only."
    assert row["receipt"]["authority"]["may_finalize"] is False
    assert await store.get_task_memory_count() == 0
    await store.close()


@pytest.mark.asyncio
async def test_identity_and_receipt_conflicts_fail_closed(tmp_path):
    store = GrokSessionStore(tmp_path / "conflict.db")
    start = _start()
    await store.begin_provider_attempt(start)

    changed = start.model_copy(update={"delegation_id": "other-delegation"})
    with pytest.raises(ValueError, match="identity conflicts"):
        await store.begin_provider_attempt(changed)

    wrong_request = _request(request_id="other-request")
    wrong_start = _start(
        attempt_id="attempt-2",
        ordinal=2,
        request=wrong_request,
    )
    with pytest.raises(ValueError, match="request_id"):
        await store.complete_provider_attempt(start.attempt_id, _returned(wrong_start))
    await store.close()


@pytest.mark.asyncio
async def test_request_identity_cannot_be_reused_or_receipt_transplanted(tmp_path):
    store = GrokSessionStore(tmp_path / "transplant.db")
    shared_request = _request(request_id="physical-request-1")
    first = _start(request=shared_request)
    second_same_request = _start(
        attempt_id="attempt-2",
        delegation_id="delegation-2",
        request=shared_request,
    )
    await store.begin_provider_attempt(first)
    with pytest.raises(ValueError, match="identity conflicts"):
        await store.begin_provider_attempt(second_same_request)

    second = _start(
        attempt_id="attempt-2",
        delegation_id="delegation-2",
        request=_request(request_id="physical-request-2"),
    )
    await store.begin_provider_attempt(second)
    with pytest.raises(ValueError, match="request_id"):
        await store.complete_provider_attempt(second.attempt_id, _returned(first))
    await store.close()


@pytest.mark.asyncio
async def test_each_physical_channel_attempt_has_its_own_ordered_row(tmp_path):
    store = GrokSessionStore(tmp_path / "physical.db")
    first_request = _request(request_id="request-google-1", model="gemini-3.1-pro")
    first = _start(
        attempt_id="attempt-google-1",
        delegation_id="delegation-google",
        ordinal=1,
        request=first_request,
        provider=ProviderId.GOOGLE,
        channel=ProviderChannel.GEMINI_API_KEY,
        model="gemini-3.1-pro",
    )
    second_request = _request(
        request_id="request-google-2",
        model="gemini-3.1-pro",
    )
    second = _start(
        attempt_id="attempt-google-2",
        delegation_id="delegation-google",
        ordinal=2,
        request=second_request,
        provider=ProviderId.GOOGLE,
        channel=ProviderChannel.VERTEX_ADC,
        model="gemini-3.1-pro",
    )

    await store.begin_provider_attempt(first)
    await store.complete_provider_attempt(first.attempt_id, _failed(first))
    await store.begin_provider_attempt(second)
    await store.complete_provider_attempt(second.attempt_id, _returned(second))

    rows = await store.list_provider_attempts(delegation_id="delegation-google")
    assert [(row["attempt_ordinal"], row["channel"], row["transport_status"]) for row in rows] == [
        (1, "gemini_api_key", "failed"),
        (2, "vertex_adc", "returned"),
    ]
    await store.close()


@pytest.mark.asyncio
async def test_delegation_listing_uses_physical_attempt_ordinal(tmp_path):
    store = GrokSessionStore(tmp_path / "ordering.db")
    later = _start(
        attempt_id="attempt-later",
        delegation_id="delegation-order",
        ordinal=2,
        request=_request(request_id="request-later"),
    )
    earlier = _start(
        attempt_id="attempt-earlier",
        delegation_id="delegation-order",
        ordinal=1,
        request=_request(request_id="request-earlier"),
    )
    await store.begin_provider_attempt(later)
    await store.begin_provider_attempt(earlier)
    rows = await store.list_provider_attempts(delegation_id="delegation-order")
    assert [row["attempt_id"] for row in rows] == ["attempt-earlier", "attempt-later"]
    await store.close()


@pytest.mark.asyncio
async def test_crash_left_start_becomes_indeterminate_not_failure(tmp_path):
    store = GrokSessionStore(tmp_path / "stale.db")
    start = _start()
    await store.begin_provider_attempt(start)
    count = await store.mark_stale_provider_attempts_indeterminate(
        datetime.now(UTC) + timedelta(seconds=1)
    )
    assert count == 1
    row = (await store.list_provider_attempts())[0]
    assert row["transport_status"] == "indeterminate"
    assert row["error_code"] == "attempt_interrupted"
    assert row["harvest_status"] == "pending"
    await store.close()


@pytest.mark.asyncio
async def test_secret_bearing_prompt_is_rejected_before_attempt_is_persisted(
    tmp_path, monkeypatch
):
    management_secret = "xai-management-secret-that-must-not-persist"
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", management_secret)
    db_path = tmp_path / "redaction.db"
    store = GrokSessionStore(db_path)
    request = _request(content=f"Never persist {management_secret}")
    start = _start(request=request)
    with pytest.raises(ValueError, match="prompt contains secret-like content"):
        await store.begin_provider_attempt(start)
    assert await store.list_provider_attempts() == []
    await store.close()

    persisted = b"".join(path.read_bytes() for path in tmp_path.glob("redaction.db*"))
    assert management_secret.encode() not in persisted


@pytest.mark.asyncio
async def test_secret_bearing_worker_output_is_redacted_before_storage(
    tmp_path, monkeypatch
):
    anthropic_secret = "sk-ant-secret-value-that-must-not-persist"
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_secret)
    db_path = tmp_path / "output-redaction.db"
    store = GrokSessionStore(db_path)
    start = _start()
    await store.begin_provider_attempt(start)
    await store.complete_provider_attempt(
        start.attempt_id,
        _returned(start, text=f"Worker echoed {anthropic_secret}"),
    )
    row = (await store.list_provider_attempts())[0]
    assert anthropic_secret not in row["output_text"]
    assert row["prompt_redaction"] == "clean"
    assert row["output_redaction"] == "redacted"
    await store.close()

    persisted = b"".join(
        path.read_bytes() for path in tmp_path.glob("output-redaction.db*")
    )
    assert anthropic_secret.encode() not in persisted


@pytest.mark.asyncio
async def test_schema_has_no_semantic_success_or_verified_column(tmp_path):
    store = GrokSessionStore(tmp_path / "schema.db")
    await store._ensure_initialized()
    async with store._conn.execute("PRAGMA table_info(provider_attempts)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    assert "success" not in columns
    assert "verified" not in columns
    assert "outcome" not in columns
    assert {"transport_status", "harvest_status", "supervisor"} <= columns
    await store.close()


@pytest.mark.asyncio
async def test_list_fails_closed_when_persisted_evidence_is_tampered(tmp_path):
    store = GrokSessionStore(tmp_path / "tamper.db")
    start = _start()
    await store.begin_provider_attempt(start)
    await store._conn.execute(
        "UPDATE provider_attempts SET prompt_text = 'tampered' WHERE attempt_id = ?",
        (start.attempt_id,),
    )
    await store._conn.commit()
    with pytest.raises(ValueError, match="prompt digest mismatch"):
        await store.list_provider_attempts()
    await store.close()


@pytest.mark.asyncio
async def test_terminal_projection_columns_are_receipt_bound(tmp_path):
    store = GrokSessionStore(tmp_path / "projection-tamper.db")
    start = _start()
    await store.begin_provider_attempt(start)
    await store.complete_provider_attempt(start.attempt_id, _returned(start))
    await store._conn.execute(
        "UPDATE provider_attempts SET resolved_model = 'claude-forged', "
        "duration_ms = 999999, finish_reason = 'content_filter' "
        "WHERE attempt_id = ?",
        (start.attempt_id,),
    )
    await store._conn.commit()
    with pytest.raises(ValueError, match="projection mismatch"):
        await store.list_provider_attempts()
    await store.close()


@pytest.mark.asyncio
async def test_lifecycle_times_and_redaction_states_are_digest_bound(tmp_path):
    store = GrokSessionStore(tmp_path / "lifecycle-tamper.db")
    start = _start()
    await store.begin_provider_attempt(start)
    await store.complete_provider_attempt(start.attempt_id, _returned(start))
    await store._conn.execute(
        "UPDATE provider_attempts SET started_at = 'not-a-time', "
        "completed_at = '1999-01-01T00:00:00+00:00', "
        "output_redaction = 'redacted' WHERE attempt_id = ?",
        (start.attempt_id,),
    )
    await store._conn.commit()
    with pytest.raises(ValueError, match="start time|completion time|redaction"):
        await store.list_provider_attempts()
    await store.close()

    store = GrokSessionStore(tmp_path / "indeterminate-tamper.db")
    interrupted = _start()
    await store.begin_provider_attempt(interrupted)
    await store.mark_stale_provider_attempts_indeterminate(
        datetime.now(UTC) + timedelta(seconds=1)
    )
    await store._conn.execute(
        "UPDATE provider_attempts SET error_kind = 'transport', "
        "completed_at = 'not-a-time', output_redaction = 'withheld' "
        "WHERE attempt_id = ?",
        (interrupted.attempt_id,),
    )
    await store._conn.commit()
    with pytest.raises(ValueError, match="completion time|orphaned output|invalid error"):
        await store.list_provider_attempts()
    await store.close()


@pytest.mark.asyncio
async def test_started_and_terminal_rows_reject_schema_invalid_receipts(tmp_path):
    store = GrokSessionStore(tmp_path / "receipt-tamper.db")
    started = _start()
    await store.begin_provider_attempt(started)
    await store._conn.execute(
        "UPDATE provider_attempts SET receipt_json = '{}', receipt_digest = ? "
        "WHERE attempt_id = ?",
        ("sha256:" + hashlib.sha256(b"{}").hexdigest(), started.attempt_id),
    )
    await store._conn.commit()
    with pytest.raises(ValueError, match="started provider attempt has terminal evidence"):
        await store.list_provider_attempts()
    await store.close()

    store = GrokSessionStore(tmp_path / "terminal-receipt-tamper.db")
    completed = _start()
    await store.begin_provider_attempt(completed)
    await store.complete_provider_attempt(completed.attempt_id, _returned(completed))
    row = (await store.list_provider_attempts())[0]
    completion = json.loads(row["completion_json"])
    completion["receipt"] = {}
    receipt_json = "{}"
    receipt_digest = "sha256:" + hashlib.sha256(receipt_json.encode()).hexdigest()
    completion["receipt_digest"] = receipt_digest
    completion_json = json.dumps(
        completion, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    completion_digest = "sha256:" + hashlib.sha256(completion_json.encode()).hexdigest()
    await store._conn.execute(
        "UPDATE provider_attempts SET receipt_json = ?, receipt_digest = ?, "
        "completion_json = ?, completion_digest = ? WHERE attempt_id = ?",
        (
            receipt_json,
            receipt_digest,
            completion_json,
            completion_digest,
            completed.attempt_id,
        ),
    )
    await store._conn.commit()
    with pytest.raises(ValueError, match="receipt violates its contract"):
        await store.list_provider_attempts()
    await store.close()


@pytest.mark.asyncio
async def test_v15_refuses_to_certify_an_incompatible_preexisting_table(tmp_path):
    db_path = tmp_path / "partial-v15.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE provider_attempts (
                id INTEGER PRIMARY KEY,
                attempt_id TEXT,
                delegation_id TEXT,
                attempt_ordinal INTEGER,
                request_id TEXT,
                supervisor TEXT,
                harvest_status TEXT
            );
            PRAGMA user_version = 14;
            """
        )
    finally:
        connection.close()

    store = GrokSessionStore(db_path)
    with pytest.raises(RuntimeError, match="refusing to certify unknown schema"):
        await store._ensure_initialized()
    await store.close()

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(provider_attempts)").fetchall()
        }
        assert columns == {
            "id",
            "attempt_id",
            "delegation_id",
            "attempt_ordinal",
            "request_id",
            "supervisor",
            "harvest_status",
        }
    finally:
        connection.close()
