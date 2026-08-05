from __future__ import annotations

import pytest

from unigrok_public import autonomy, server, state
from unigrok_public.state import PublicStateStore


def test_continue_cream_is_host_visible_but_not_committed() -> None:
    envelope = autonomy.continue_envelope(
        job_id="job-cream",
        continue_token="a" * 32,
        ledger_cursor=4,
        acceptance_hash_value="b" * 64,
        gaps=["missing_checklist"],
        text="Verifier rejected CommitDone.",
        poll=False,
    )
    result = autonomy.apply_continue_cream(envelope, "Draft answer")

    assert result["status"] == "continue"
    assert result["autonomy"]["committed"] is False
    assert result["proposed_text"] == "Draft answer"
    assert result["status_text"] == "Verifier rejected CommitDone."
    assert result["text"] == (
        "Draft answer\n\n[continue · not committed]\n"
        "Verifier rejected CommitDone."
    )


def test_continue_cream_does_not_relabel_terminal_payloads() -> None:
    payload = {
        "status": "error",
        "text": "Terminal mission failure.",
        "autonomy": {
            "protocol": "unigrok_continue_v1",
            "committed": False,
        },
    }

    assert autonomy.apply_continue_cream(payload, "Rejected draft") == payload


def test_empty_continue_cream_preserves_status_message() -> None:
    envelope = autonomy.continue_envelope(
        job_id="job-empty",
        continue_token="c" * 32,
        ledger_cursor=0,
        acceptance_hash_value="d" * 64,
        text="Continue later.",
        poll=False,
    )

    assert autonomy.apply_continue_cream(envelope, "") == envelope
    assert autonomy.host_visible_continue_text("", "Continue later.") == "Continue later."


def test_legacy_continue_payload_is_redacted_and_bounded() -> None:
    synthetic_marker = "xai-" + ("a" * 16)
    large = synthetic_marker + ("x" * (state.DURABLE_TEXT_MAX_BYTES + 1024))
    payload = {
        "status": "continue",
        "text": large,
        "proposed_text": large,
        "status_text": large,
        "autonomy": {
            "protocol": "unigrok_continue_v1",
            "committed": False,
        },
    }

    durable = state._durable_agent_payload(payload)

    for field in ("text", "proposed_text", "status_text"):
        value = durable[field]
        assert synthetic_marker not in value
        assert len(value.encode("utf-8")) <= state.DURABLE_TEXT_MAX_BYTES


@pytest.mark.asyncio
async def test_legacy_rejected_draft_is_cream_first_and_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = PublicStateStore(tmp_path / "cream-first.db")
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", True)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", False)

    async def thin_turn(**_kwargs: object) -> dict:
        return {
            "text": "healthz",
            "plane": "test",
            "resolved_plane": "test",
            "stop_reason": "EndTurn",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", thin_turn)
    result = await server.agent(
        task="Return a checklist of deploy steps including healthz",
        session="cream:first",
    )

    assert result["status"] == "continue"
    assert result["autonomy"]["committed"] is False
    assert result["proposed_text"] == "healthz"
    assert result["text"].startswith(
        "healthz\n\n[continue · not committed]\n"
    )
    assert "Acceptance checker rejected ProposeDone" in result["status_text"]
    assert await store.load_messages("cream:first") == []
