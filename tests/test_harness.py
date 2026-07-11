from unittest.mock import AsyncMock, patch

import pytest

from src import utils
from src.http_server import public_agent
from src.utils import MetaLayer, run_agent_turn


@pytest.mark.asyncio
async def test_run_agent_turn_calls_orchestrate(monkeypatch):
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123"))
    monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

    layer = await run_agent_turn(messages=[{"role": "user", "content": "build this"}])

    assert layer.generation == "done"
    args, kwargs = mock_orchestrate.call_args
    assert kwargs["prompt"] == "build this"
    assert kwargs["context_id"] == "ctx-123"
    assert kwargs["enable_agentic"] is True


@pytest.mark.asyncio
async def test_run_agent_turn_preserves_openai_message_context(monkeypatch):
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123"))
    monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

    await run_agent_turn(
        messages=[
            {"role": "system", "content": "Answer with careful context."},
            {"role": "user", "content": "My name is David."},
            {"role": "assistant", "content": "Nice to meet you, David."},
            {"role": "tool", "name": "memory", "content": "No saved external profile."},
            {"role": "user", "content": "What is my name?"},
        ]
    )

    _, kwargs = mock_orchestrate.call_args
    assert kwargs["prompt"] == "What is my name?"
    assert kwargs["input_messages"] == [
        {"role": "user", "content": "My name is David."},
        {"role": "assistant", "content": "Nice to meet you, David."},
        {"role": "tool", "content": "No saved external profile.", "name": "memory"},
        {"role": "user", "content": "What is my name?"},
    ]
    assert "Additional Instructions:" in kwargs["dynamic_sys_prompt"]
    assert "Answer with careful context." in kwargs["dynamic_sys_prompt"]


@pytest.mark.asyncio
async def test_run_agent_turn_preserves_image_parts(monkeypatch):
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123"))
    monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

    await run_agent_turn(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "https://example.test/image.png", "detail": "high"}},
                ],
            }
        ]
    )

    _, kwargs = mock_orchestrate.call_args
    assert kwargs["prompt"] == "Describe this\n[image: https://example.test/image.png]"
    assert kwargs["input_messages"][0]["content"][1]["image_url"]["url"] == "https://example.test/image.png"


@pytest.mark.asyncio
async def test_run_agent_turn_persists_session_turn(monkeypatch):
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    monkeypatch.setattr(
        "src.utils.orchestrate",
        AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123", plane="API", tokens=5, cost_usd=0.01)),
    )
    monkeypatch.setattr("src.utils.load_history", AsyncMock(return_value=[]))
    mock_append = AsyncMock()
    monkeypatch.setattr("src.utils.append_and_save_history", mock_append)
    mock_save_session = AsyncMock()
    monkeypatch.setattr("src.utils.store.save_session", mock_save_session)

    layer = await run_agent_turn(prompt="remember this", session="s1")

    assert layer.generation == "done"
    mock_append.assert_awaited_once()
    _, _, saved_prompt, saved_reply, _ = mock_append.await_args.args
    metadata = mock_append.await_args.kwargs["metadata"]
    assert saved_prompt == "remember this"
    assert saved_reply == "done"
    assert metadata["context_id"] == "ctx-123"
    # model defaults to None now — orchestrate auto-selects, and save_session
    # simply skips the model column update for None.
    mock_save_session.assert_awaited_once_with("s1", api_thread_id="s1", model=None)


@pytest.mark.asyncio
async def test_run_agent_turn_model_none_lets_orchestrate_autoselect(monkeypatch):
    """Now#4: the gateway boundary no longer pins DEFAULT_PLANNING_MODEL —
    model=None flows through so orchestrate() picks the model itself."""
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123"))
    monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

    await run_agent_turn(prompt="hello there")

    _, kwargs = mock_orchestrate.call_args
    assert kwargs["requested_model"] is None
    assert kwargs["mode"] == "auto"
    assert kwargs["thinking_mode"] is False


@pytest.mark.asyncio
async def test_run_agent_turn_forwards_model_mode_and_thinking(monkeypatch):
    """Explicit model/mode/thinking_mode must pass through unchanged — the
    HTTP gateway still pins model=... today and must keep working."""
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123"))
    monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

    await run_agent_turn(prompt="hello", model="grok-4.3", mode="reasoning", thinking_mode=True)

    _, kwargs = mock_orchestrate.call_args
    assert kwargs["requested_model"] == "grok-4.3"
    assert kwargs["mode"] == "reasoning"
    assert kwargs["thinking_mode"] is True


@pytest.mark.asyncio
async def test_run_agent_turn_forwards_enable_agentic(monkeypatch):
    """enable_agentic passes through to orchestrate so the MCP agent tool's
    'fast' mode can select the toolless path without env mutation."""
    monkeypatch.setattr(
        "src.utils.get_dynamic_context",
        AsyncMock(return_value=("system context", True, "ctx-123")),
    )
    mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="done", context_id="ctx-123"))
    monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

    await run_agent_turn(prompt="hello", enable_agentic=False)

    _, kwargs = mock_orchestrate.call_args
    assert kwargs["enable_agentic"] is False


@pytest.mark.asyncio
async def test_public_agent_uses_run_agent_turn(monkeypatch):
    """public_agent routes through the shared harness with auto-routing
    defaults (model=None) and returns the structured payload."""
    mock_run = AsyncMock(return_value=MetaLayer(generation="public answer"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    result = await public_agent("hello", session="s1")

    assert result.response == "public answer"
    mock_run.assert_awaited_once_with(
        prompt="hello",
        session="s1",
        system_prompt=None,
        model=None,
        mode="auto",
        thinking_mode=False,
        enable_agentic=True,
        plane="auto",
        fallback_policy="cross_plane",
    )
    assert result.requested_mode == "auto"
    assert result.mode_source == "default"


@pytest.mark.asyncio
async def test_cloudrun_orchestrate_does_not_cli_fallback(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    mock_call_plane = AsyncMock(side_effect=RuntimeError("api failed"))
    monkeypatch.setattr("src.utils._call_plane", mock_call_plane)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with pytest.raises(RuntimeError, match="Cloud Run"):
            await utils.orchestrate(
                prompt="hello",
                dynamic_sys_prompt="system",
                store=None,
                enable_agentic=False,
                requested_model="grok-4.3",
            )

    mock_exec.assert_not_called()
    assert mock_call_plane.await_count == 1
