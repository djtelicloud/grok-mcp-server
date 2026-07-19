import asyncio

import pytest

from unigrok_public import autonomy, server


@pytest.fixture
def autonomy_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", True)


def test_acceptance_hash_is_stable_and_normalized() -> None:
    assert autonomy.acceptance_hash("Hello World") == autonomy.acceptance_hash(
        "  hello   world "
    )
    assert autonomy.acceptance_hash("a") != autonomy.acceptance_hash("b")


def test_artifact_hash_matches_persisted_normalization() -> None:
    raw = "  hello secret\n"
    digest = autonomy.artifact_hash(raw, kind="answer")
    assert digest == autonomy.artifact_hash(
        autonomy.normalize_artifact_content(raw), kind="answer"
    )


def test_check_propose_done_rejects_empty_and_nonanswer() -> None:
    empty = autonomy.check_propose_done(
        acceptance_text="Return a checklist of deploy steps",
        answer_text="",
        evidence_contents=[],
    )
    assert empty["ok"] is False
    assert "empty_answer" in empty["gaps"]
    assert "missing_evidence" in empty["gaps"]

    nonanswer = autonomy.check_propose_done(
        acceptance_text="Return a checklist of deploy steps",
        answer_text="I'll put together the checklist next.",
        evidence_contents=["I'll put together the checklist next."],
    )
    assert nonanswer["ok"] is False
    assert "nonanswer_completion" in nonanswer["gaps"]


def test_check_propose_done_rejects_token_echo_checklist() -> None:
    result = autonomy.check_propose_done(
        acceptance_text="Return a checklist of deploy steps including healthz",
        answer_text="healthz",
        evidence_contents=["healthz"],
    )
    assert result["ok"] is False
    assert any(
        gap.startswith("checklist_") or gap in {"answer_too_short", "token_echo"}
        for gap in result["gaps"]
    )


def test_check_propose_done_literal_exact_match() -> None:
    result = autonomy.check_propose_done(
        acceptance_text="Reply with exactly MCP_LIVE_OK",
        answer_text="MCP_LIVE_OK",
        evidence_contents=[],
    )
    assert result["ok"] is True
    assert result["gaps"] == []
    assert result.get("literal_match") is True
    assert result.get("task_class") == "literal"


def test_check_propose_done_literal_mismatch() -> None:
    result = autonomy.check_propose_done(
        acceptance_text="Reply with exactly MCP_LIVE_OK",
        answer_text="Nope",
        evidence_contents=[],
    )
    assert result["ok"] is False
    assert "literal_mismatch" in result["gaps"]
    assert "answer_too_short" not in result["gaps"]


def test_check_propose_done_accepts_structured_checklist() -> None:
    answer = (
        "- Build the container image\n"
        "- Run database migrations\n"
        "- Smoke test /healthz\n"
        "- Flip traffic to the new revision\n"
    )
    result = autonomy.check_propose_done(
        acceptance_text="Return a checklist of deploy steps including healthz",
        answer_text=answer,
        evidence_contents=[answer],
    )
    assert result["ok"] is True
    assert result["gaps"] == []


def test_check_propose_done_skips_heavy_coverage_for_short_task_text() -> None:
    answer = (
        "Findings: root cause is a null pointer in auth during token refresh. "
        "Patch the guard and add a regression test."
    )
    result = autonomy.check_propose_done(
        acceptance_text="Review the failure",
        answer_text=answer,
        evidence_contents=[answer],
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_api_jobs_stay_pending_not_continue(
    monkeypatch: pytest.MonkeyPatch, autonomy_on: None
) -> None:
    async def slow() -> dict:
        await asyncio.Event().wait()
        return {"status": "complete", "files": []}

    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0)

    async def fake_await(task, ctx, wait_seconds):  # noqa: ANN001
        return None

    monkeypatch.setattr(server, "_await_job_window", fake_await)
    pending = await server._run_durable_job(slow, ctx=None, kind="xai_list_files")
    assert pending["status"] == "pending"
    assert "continue_token" not in pending


@pytest.mark.asyncio
async def test_continue_token_reattaches_in_flight_quantum(
    monkeypatch: pytest.MonkeyPatch, autonomy_on: None
) -> None:
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0)
    release = asyncio.Event()

    async def slow_turn(**_kwargs: object) -> dict:
        await release.wait()
        answer = (
            "- Build the container image\n"
            "- Run database migrations\n"
            "- Smoke test /healthz\n"
            "- Flip traffic to the new revision\n"
        )
        return {
            "text": answer,
            "plane": "test",
            "stop_reason": "EndTurn",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", slow_turn)
    first = await server.agent(
        task="Return a checklist of deploy steps including healthz"
    )
    assert first["status"] == "continue"
    token = first["continue_token"]
    job_id = first["job_id"]

    reattached = await server.agent(continue_token=token)
    assert reattached["status"] == "continue"
    assert reattached["job_id"] == job_id
    # Lease must be released so a third attach is not blocked.
    again = await server.agent(continue_token=token)
    assert again.get("autonomy", {}).get("claim_blocked") is not True

    release.set()
    complete = await server.agent_result(job_id)
    assert complete["status"] == "complete"
    assert complete["job_id"] == job_id
    assert complete["autonomy"]["committed"] is True


@pytest.mark.asyncio
async def test_failed_propose_done_persists_needs_continuation(
    monkeypatch: pytest.MonkeyPatch, autonomy_on: None
) -> None:
    async def empty_turn(**_kwargs: object) -> dict:
        return {
            "text": "",
            "plane": "test",
            "stop_reason": "EndTurn",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", empty_turn)
    result = await server.agent(task="Return a checklist of deploy steps")
    assert result["status"] == "continue"
    assert result["autonomy"]["committed"] is False
    assert "empty_answer" in result["autonomy"]["gaps"]
    stored = await server.STATE.load_agent_job(result["job_id"])
    assert stored is not None
    assert stored["status"] == "needs_continuation"
    # Enrichment must not downgrade terminal status (review-shaped bug).
    await server.STATE.merge_agent_job_enrichment(
        result["job_id"], {"review_kind": "pull_request"}
    )
    stored_after = await server.STATE.load_agent_job(result["job_id"])
    assert stored_after is not None
    assert stored_after["status"] == "needs_continuation"
    polled = await server.agent_result(result["job_id"])
    assert polled["status"] == "continue"
    assert polled["review_kind"] == "pull_request"


@pytest.mark.asyncio
async def test_exception_text_is_redacted(
    monkeypatch: pytest.MonkeyPatch, autonomy_on: None
) -> None:
    async def boom(**_kwargs: object) -> dict:
        raise RuntimeError("xai-secret-test-credential-12345 leaked")

    monkeypatch.setattr(server, "_execute_team_turn", boom)
    result = await server.agent(task="anything")
    assert result["status"] == "error"
    assert "xai-secret-test-credential-12345" not in result["text"]
    assert "REDACTED" in result["text"] or "[REDACTED" in result["text"]


@pytest.mark.asyncio
async def test_continue_restores_original_task_not_acceptance(
    monkeypatch: pytest.MonkeyPatch, autonomy_on: None
) -> None:
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0)
    captured: dict = {}
    release = asyncio.Event()
    calls = 0

    async def turn(**kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        captured.update(kwargs)
        if calls == 1:
            await release.wait()
            return {
                "text": "healthz",
                "plane": "test",
                "stop_reason": "EndTurn",
                "workspace_attached": False,
                "cost_usd": 0.0,
                "orchestration": {},
            }
        return {
            "text": (
                "- Build image\n- Migrate\n- Smoke /healthz\n- Flip traffic\n"
            ),
            "plane": "test",
            "stop_reason": "EndTurn",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", turn)
    first = await server.agent(
        task="Ship the service carefully",
        acceptance="Return a checklist of deploy steps including healthz",
        workspace_context="## Diff\n+ critical path",
        disable_tools=["web"],
    )
    assert first["status"] == "continue"
    release.set()
    # Finish first quantum (thin answer → needs_continuation).
    await server.agent_result(first["job_id"], wait_seconds=5)
    second = await server.agent(continue_token=first["continue_token"])
    # Either continue (still working) or complete; prompt must be original task.
    assert captured.get("prompt") == "Ship the service carefully"
    assert "critical path" in str(captured.get("workspace_context") or "")
    assert captured.get("allow_web") is False
    assert second["status"] in {"continue", "complete", "pending"}
