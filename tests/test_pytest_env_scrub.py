"""run_local_tests must not inherit server-owned secrets into pytest children."""

from __future__ import annotations

import pytest

from src.credentials import SERVER_OWNED_SECRET_ENV_NAMES
from src.tools import system as system_tools


@pytest.mark.asyncio
async def test_run_local_tests_passes_scrubbed_env(monkeypatch, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        system_tools.PathResolver,
        "get_workspace_root",
        staticmethod(lambda: root),
    )
    monkeypatch.setattr(system_tools, "is_cloudrun_runtime", lambda: False)
    monkeypatch.setattr(system_tools.shutil, "which", lambda _name: None)
    monkeypatch.setenv("XAI_API_KEY", "xai-should-not-reach-pytest")

    captured: dict = {}

    async def _fake_exec(*cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cmd"] = cmd

        class _Proc:
            returncode = 0

            async def wait(self):
                return 0

            def kill(self):
                return None

        return _Proc()

    async def _fake_communicate(proc, timeout):
        return (b"1 passed\n", b"")

    monkeypatch.setattr(system_tools.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(system_tools, "communicate_with_timeout", _fake_communicate)

    out = await system_tools.run_local_tests(target="tests")
    assert "passed" in out
    env = captured["env"]
    assert isinstance(env, dict)
    for name in SERVER_OWNED_SECRET_ENV_NAMES:
        assert name not in env
    assert "XAI_API_KEY" not in env
