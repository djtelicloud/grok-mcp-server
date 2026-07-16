import asyncio
import json
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


def test_scrubbed_subprocess_env_rejects_named_and_unknown_secret_families():
    child = scrubbed_subprocess_env(
        {
            "PATH": "/safe/bin",
            "LANG": "C.UTF-8",
            "GH_TOKEN": "github-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "FUTURE_CLOUD_ACCESS_KEY_ID": "future-access-key-id",
            "DATABASE_URL": "postgres://user:pass@example/db",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "FUTURE_PROVIDER_API_KEY": "future-secret",
            "SOME_CLIENT_SECRET": "client-secret",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )

    assert child == {
        "PATH": "/safe/bin",
        "LANG": "C.UTF-8",
        "TOKENIZERS_PARALLELISM": "false",
    }


def test_scrubbed_subprocess_env_allows_only_explicit_reviewed_secret():
    child = scrubbed_subprocess_env(
        {
            "PATH": "/safe/bin",
            "CLAUDE_CODE_OAUTH_TOKEN": "subscription-oauth",
            "XAI_API_KEY": "server-api-secret",
        },
        allow_secret_names={"CLAUDE_CODE_OAUTH_TOKEN"},
    )

    assert child == {
        "PATH": "/safe/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": "subscription-oauth",
    }


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


def test_redact_secrets_removes_invalid_principal_map_values(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        '{"not-a-valid-principal":"mapped-opaque-secret",'
        '"not-a-valid-principal":"duplicate-opaque-secret"}',
    )

    assert redact_secrets(
        "failure: mapped-opaque-secret duplicate-opaque-secret"
    ) == "failure: [REDACTED] [REDACTED]"


def test_redact_secrets_removes_overflow_principal_map_values(monkeypatch):
    entries = {f"invalid-{index}": f"mapped-secret-{index}" for index in range(257)}
    entries["invalid-256"] = "mapped-overflow-secret"
    monkeypatch.setenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", json.dumps(entries))

    assert redact_secrets("failure: mapped-overflow-secret") == (
        "failure: [REDACTED]"
    )


def test_redact_secrets_fails_closed_for_too_large_principal_map(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps(
            {
                "invalid-target": "mapped-too-large-secret",
                "padding": "x" * 66_000,
            }
        ),
    )

    assert redact_secrets("failure: mapped-too-large-secret") == "[REDACTED]"


def test_redact_secrets_removes_individual_gateway_keys(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_API_KEYS", "first-gateway-secret, second-gateway-secret"
    )

    assert redact_secrets("failure: second-gateway-secret") == "failure: [REDACTED]"


def test_redact_secrets_splits_gateway_keys_case_insensitively(monkeypatch):
    monkeypatch.setenv(
        "Unigrok_Api_Keys", "first-mixed-gateway, second-mixed-gateway"
    )

    assert redact_secrets("failure: second-mixed-gateway") == "failure: [REDACTED]"


def test_redact_secrets_splits_stable_gateway_key_records(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_API_KEY_RECORDS",
        '{"owner":"stable-owner-secret","reviewer":"stable-reviewer-secret"}',
    )

    assert redact_secrets("failure: stable-reviewer-secret") == (
        "failure: [REDACTED]"
    )


def test_redact_secrets_replaces_longer_prefix_keys_first(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_API_KEYS", "shared-prefix-key,shared-prefix-key-with-suffix"
    )

    assert redact_secrets("failure: shared-prefix-key-with-suffix") == (
        "failure: [REDACTED]"
    )


def test_redact_secrets_removes_unknown_secret_shaped_env_value(monkeypatch):
    monkeypatch.setenv("FUTURE_PROVIDER_API_KEY", "future-provider-secret")

    assert redact_secrets("failure: future-provider-secret") == (
        "failure: [REDACTED]"
    )


def test_redact_secrets_removes_individual_principal_xai_keys(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        '{"oauth:https%3A%2F%2Fcontrol.grokmcp.org:github%3A42":"opaque-principal-credential"}',
    )

    assert redact_secrets("failure: opaque-principal-credential") == (
        "failure: [REDACTED]"
    )
