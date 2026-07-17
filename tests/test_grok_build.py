from pathlib import Path
from typing import Any

import pytest

from unigrok_public.grok_build import (
    LOCAL_AUTHORITY_TOOLS,
    GrokBuildACPManager,
    GrokBuildWorker,
    _normalize_stop_reason,
    _permission_reject_result,
)


def _worker(tmp_path: Path, *, agentic: bool = True, allow_web: bool = True) -> GrokBuildWorker:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    return GrokBuildWorker(
        binary="/usr/local/bin/grok",
        auth_path=auth,
        model="grok-live",
        effort="high",
        max_turns=6,
        allow_web=allow_web,
        agentic=agentic,
        system_prompt="safe",
        timeout_seconds=120,
    )


def test_worker_removes_local_authority_tools_from_model_surface(tmp_path: Path) -> None:
    command = _worker(tmp_path)._command()
    index = command.index("--disallowed-tools")
    assert command[index + 1] == LOCAL_AUTHORITY_TOOLS
    assert "--deny" not in command
    assert "read_file" in LOCAL_AUTHORITY_TOOLS
    assert "run_terminal_cmd" in LOCAL_AUTHORITY_TOOLS


def test_chat_profile_forces_verbatim_and_empty_tools(tmp_path: Path) -> None:
    command = _worker(tmp_path, agentic=False, allow_web=False)._command()
    assert "--verbatim" in command
    assert "--tools" in command
    assert command[command.index("--tools") + 1] == ""
    assert "--disable-web-search" in command


def test_permission_reject_prefers_reject_always() -> None:
    result = _permission_reject_result(
        [
            {"optionId": "allow-once", "kind": "allow_once"},
            {"optionId": "reject-once", "kind": "reject_once"},
            {"optionId": "reject-always", "kind": "reject_always"},
        ]
    )
    assert result == {
        "outcome": {"outcome": "selected", "optionId": "reject-always"}
    }


def test_permission_reject_falls_back_to_cancelled_without_reject_options() -> None:
    assert _permission_reject_result(
        [{"optionId": "allow-once", "kind": "allow_once"}]
    ) == {"outcome": {"outcome": "cancelled"}}


def test_normalize_stop_reason_accepts_endturn_variants() -> None:
    assert _normalize_stop_reason("EndTurn") == "end_turn"
    assert _normalize_stop_reason("end-turn") == "end_turn"
    assert _normalize_stop_reason("Cancelled") == "cancelled"


def test_build_runtime_environment_excludes_api_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "must-not-reach-build")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-build")
    worker = _worker(tmp_path)
    _, env = worker._runtime_env()
    assert "XAI_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["GROK_AUTH_PATH"] == str(worker.auth_path)
    assert worker._runtime is not None
    worker._runtime.cleanup()


@pytest.mark.asyncio
async def test_build_acp_returns_only_final_message_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = _worker(tmp_path)
    worker._work = tmp_path

    async def fake_start() -> None:
        return None

    async def fake_request(
        method: str, params: dict[str, Any], *, deadline_seconds: float
    ) -> dict[str, Any]:
        if method == "session/new":
            return {"sessionId": "session-1"}
        state = worker._turns["session-1"]
        state.update(
            {"sessionUpdate": "agent_message_chunk", "content": {"text": "I'll search."}}
        )
        state.update({"sessionUpdate": "tool_call"})
        state.update({"sessionUpdate": "agent_thought_chunk"})
        state.update(
            {"sessionUpdate": "agent_message_chunk", "content": {"text": "FINAL ANSWER"}}
        )
        return {
            "stopReason": "end_turn",
            "_meta": {
                "modelId": "grok-live",
                "requestId": "request-1",
                "usage": {"totalTokens": 42},
            },
        }

    monkeypatch.setattr(worker, "start", fake_start)
    monkeypatch.setattr(worker, "_request", fake_request)
    result = await worker.run("research")
    assert result["text"] == "FINAL ANSWER"
    assert result["stop_reason"] == "EndTurn"
    assert result["transport"] == "persistent_acp"
    assert result["usage"]["totalTokens"] == 42


@pytest.mark.asyncio
async def test_build_acp_retries_cancelled_turn_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = _worker(tmp_path)
    worker._work = tmp_path
    prompts: list[str] = []

    async def fake_start() -> None:
        return None

    async def fake_request(
        method: str, params: dict[str, Any], *, deadline_seconds: float
    ) -> dict[str, Any]:
        if method == "session/new":
            return {"sessionId": "session-2"}
        text = params["prompt"][0]["text"]
        prompts.append(text)
        state = worker._turns["session-2"]
        if len(prompts) == 1:
            return {"stopReason": "Cancelled"}
        state.update(
            {"sessionUpdate": "agent_message_chunk", "content": {"text": "recovered"}}
        )
        return {"stopReason": "EndTurn", "_meta": {"modelId": "grok-live"}}

    monkeypatch.setattr(worker, "start", fake_start)
    monkeypatch.setattr(worker, "_request", fake_request)
    result = await worker.run("research")
    assert result["text"] == "recovered"
    assert len(prompts) == 2
    assert "Do not call any tools" in prompts[1]


@pytest.mark.asyncio
async def test_manager_recovers_cancelled_agentic_with_toolless_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    manager = GrokBuildACPManager(
        binary="/usr/local/bin/grok", auth_path=auth, timeout_seconds=30
    )
    calls: list[bool] = []

    async def fake_run(self: GrokBuildWorker, prompt: str) -> dict[str, Any]:
        calls.append(self.agentic)
        if self.agentic:
            raise RuntimeError(
                "Grok Build ended without a completed answer (stop reason: cancelled)"
            )
        return {
            "text": "toolless-ok",
            "model": "grok-live",
            "stop_reason": "EndTurn",
            "plane": "grok_build_oauth",
            "billing_class": "subscription_build",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "elapsed_ms": 10,
            "transport": "persistent_acp",
        }

    monkeypatch.setattr(GrokBuildWorker, "run", fake_run)
    result = await manager.run(
        "ping",
        model="grok-live",
        effort=None,
        max_turns=6,
        allow_web=True,
        agentic=True,
        system_prompt="safe",
    )
    assert result["text"] == "toolless-ok"
    assert calls == [True, False]
    assert result["completion_recovery"]["reason"] == "cli_cancelled_toolless_profile"


@pytest.mark.asyncio
async def test_build_timeout_sends_acp_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = _worker(tmp_path)
    worker._work = tmp_path
    sent: list[dict[str, Any]] = []

    async def fake_start() -> None:
        return None

    async def fake_request(
        method: str, params: dict[str, Any], *, deadline_seconds: float
    ) -> dict[str, Any]:
        if method == "session/new":
            return {"sessionId": "session-3"}
        raise TimeoutError

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)

    monkeypatch.setattr(worker, "start", fake_start)
    monkeypatch.setattr(worker, "_request", fake_request)
    monkeypatch.setattr(worker, "_send", fake_send)
    with pytest.raises(TimeoutError):
        await worker.run("research")
    assert sent == [
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": "session-3"},
        }
    ]
