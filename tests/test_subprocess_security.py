import asyncio
import subprocess

import pytest

from src.credentials import SERVER_OWNED_SECRET_ENV_NAMES
from src.subprocess_security import (
    create_scrubbed_subprocess_exec,
    scrubbed_subprocess_env,
    scrubbed_subprocess_run,
)
from src.utils import redact_secrets


def test_scrubbed_subprocess_env_preserves_explicit_safe_values(monkeypatch):
    for index, name in enumerate(SERVER_OWNED_SECRET_ENV_NAMES):
        monkeypatch.setenv(name, f"server-secret-{index}")

    child = scrubbed_subprocess_env(
        {
            "PATH": "/safe/bin",
            "SAFE_CHILD_SETTING": "kept",
            "XAI_API_KEY": "explicit-secret",
        }
    )

    assert child == {"PATH": "/safe/bin", "SAFE_CHILD_SETTING": "kept"}


@pytest.mark.asyncio
async def test_async_subprocess_wrapper_scrubs_environment(monkeypatch):
    captured = {}
    sentinel = object()

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return sentinel

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await create_scrubbed_subprocess_exec(
        "tool",
        "arg",
        env={"SAFE": "yes", "XAI_API_KEY": "do-not-inherit"},
    )

    assert result is sentinel
    assert captured == {"args": ("tool", "arg"), "env": {"SAFE": "yes"}}


def test_sync_subprocess_wrapper_scrubs_environment(monkeypatch):
    captured = {}
    sentinel = subprocess.CompletedProcess(["tool"], 0)

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return sentinel

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = scrubbed_subprocess_run(
        ["tool"], env={"SAFE": "yes", "UNIGROK_API_KEYS": "do-not-inherit"}
    )

    assert result is sentinel
    assert captured == {"args": (["tool"],), "env": {"SAFE": "yes"}}


def test_redact_secrets_removes_unstructured_exact_value(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "plain-provider-secret")

    assert redact_secrets("failure: plain-provider-secret") == "failure: [REDACTED]"


def test_redact_secrets_removes_individual_gateway_keys(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_API_KEYS", "first-gateway-secret, second-gateway-secret"
    )

    assert redact_secrets("failure: second-gateway-secret") == "failure: [REDACTED]"
