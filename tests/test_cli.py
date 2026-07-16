import io
import os
import sys
import types
import stat
from pathlib import Path

import pytest

import src.cli as cli
from src.cli import init_project


def test_init_project_copies_example_env_and_prints_ide_configs(tmp_path: Path):
    (tmp_path / "example.env").write_text("XAI_API_KEY=your_xai_api_key_here\n", encoding="utf-8")
    stream = io.StringIO()

    code = init_project(tmp_path, stream)

    assert code == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "XAI_API_KEY=your_xai_api_key_here\n"
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    out = stream.getvalue()
    assert "Created" in out
    assert "VS Code" in out
    assert "Claude Desktop" in out
    assert "Claude Code" in out
    assert "Codex" in out
    assert "http://localhost:4765/mcp" in out
    assert "http://localhost:4765/healthz" in out
    assert "http://localhost:4765/readyz" in out


def test_init_project_leaves_existing_env_unchanged(tmp_path: Path):
    (tmp_path / "example.env").write_text("XAI_API_KEY=template\n", encoding="utf-8")
    (tmp_path / ".env").write_text("XAI_API_KEY=real\n", encoding="utf-8")
    (tmp_path / ".env").chmod(0o600)
    stream = io.StringIO()

    code = init_project(tmp_path, stream)

    assert code == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "XAI_API_KEY=real\n"
    assert "leaving it unchanged" in stream.getvalue()


def test_installed_cli_uses_current_directory_as_project_root(monkeypatch, tmp_path: Path):
    package_root = tmp_path / "venv" / "site-packages"
    fake_cli = package_root / "src" / "cli.py"
    fake_cli.parent.mkdir(parents=True)
    workdir = tmp_path / "project"
    workdir.mkdir()

    monkeypatch.delenv("UNIGROK_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(cli, "__file__", str(fake_cli))
    monkeypatch.chdir(workdir)

    assert cli._project_root() == workdir


def test_installed_cli_never_trusts_cwd_dotenv_for_runtime(monkeypatch, tmp_path: Path):
    package_root = tmp_path / "venv" / "site-packages"
    fake_cli = package_root / "src" / "cli.py"
    fake_cli.parent.mkdir(parents=True)
    workdir = tmp_path / "untrusted-project"
    workdir.mkdir()
    (workdir / ".env").write_text(
        "\n".join(
            (
                "UNIGROK_RUNTIME=http",
                "UNIGROK_HOST=0.0.0.0",
                "UNIGROK_TRUSTED_LOOPBACK_PROXY=1",
                "UNIGROK_SERVICE_MODE=contributor",
                "UNIGROK_CONTRIBUTOR_MODE=1",
                "WORKSPACE_ROOT=/tmp/attacker-workspace",
                "ENABLE_GIT_WRITE=1",
                "XAI_API_BASE_URL=https://attacker.example/v1",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    sensitive_names = (
        "UNIGROK_RUNTIME",
        "UNIGROK_HOST",
        "UNIGROK_TRUSTED_LOOPBACK_PROXY",
        "UNIGROK_SERVICE_MODE",
        "UNIGROK_CONTRIBUTOR_MODE",
        "WORKSPACE_ROOT",
        "ENABLE_GIT_WRITE",
        "XAI_API_BASE_URL",
    )
    for name in sensitive_names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("UNIGROK_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "trusted-parent-key")
    monkeypatch.setattr(cli, "__file__", str(fake_cli))
    monkeypatch.chdir(workdir)

    called = {}
    fake_server = types.ModuleType("src.server")
    fake_server.main = lambda argv: called.setdefault("argv", list(argv))
    monkeypatch.setitem(sys.modules, "src.server", fake_server)
    import src

    monkeypatch.setattr(src, "server", fake_server, raising=False)

    assert cli._trusted_runtime_env_path() is None
    assert cli.main([]) is None
    assert called["argv"] == []
    assert os.environ["XAI_API_KEY"] == "trusted-parent-key"
    for name in sensitive_names:
        assert name not in os.environ


def test_source_cli_loads_only_its_repository_dotenv(monkeypatch, tmp_path: Path):
    source_root = tmp_path / "trusted-source"
    fake_cli = source_root / "src" / "cli.py"
    fake_cli.parent.mkdir(parents=True)
    (source_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (source_root / ".env").write_text("XAI_API_KEY=trusted-source-key\n", encoding="utf-8")
    untrusted_cwd = tmp_path / "untrusted"
    untrusted_cwd.mkdir()
    (untrusted_cwd / ".env").write_text("XAI_API_KEY=attacker-key\n", encoding="utf-8")

    monkeypatch.delenv("UNIGROK_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(cli, "__file__", str(fake_cli))
    monkeypatch.chdir(untrusted_cwd)

    assert cli._trusted_runtime_env_path() == source_root / ".env"


def test_init_project_uses_packaged_template_for_installed_cli(monkeypatch, tmp_path: Path):
    package_root = tmp_path / "venv" / "site-packages"
    fake_cli = package_root / "src" / "cli.py"
    fake_cli.parent.mkdir(parents=True)
    (package_root / "example.env").write_text("XAI_API_KEY=packaged-template\n", encoding="utf-8")
    target = tmp_path / "project"
    stream = io.StringIO()

    monkeypatch.setattr(cli, "__file__", str(fake_cli))
    code = init_project(target, stream)

    assert code == 0
    assert (target / ".env").read_text(encoding="utf-8") == "XAI_API_KEY=packaged-template\n"
    assert stat.S_IMODE((target / ".env").stat().st_mode) == 0o600
    assert "packaged environment template" in stream.getvalue()


def test_init_project_secret_env_mode_ignores_permissive_umask(tmp_path: Path):
    stream = io.StringIO()
    previous = os.umask(0)
    try:
        assert init_project(tmp_path, stream) == 0
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_trusted_env_rejects_unsafe_modes_links_and_non_files(monkeypatch, tmp_path: Path):
    for mode in (0o644, 0o660):
        path = tmp_path / f"mode-{mode:o}.env"
        path.write_text("XAI_API_KEY=not-loaded\n", encoding="utf-8")
        path.chmod(mode)
        with pytest.raises(RuntimeError, match="mode 0600"):
            cli._load_trusted_env(path)

    safe = tmp_path / "safe.env"
    safe.write_text("UNIGROK_TEST_SAFE_ENV=loaded\n", encoding="utf-8")
    safe.chmod(0o600)
    monkeypatch.delenv("UNIGROK_TEST_SAFE_ENV", raising=False)
    cli._load_trusted_env(safe)
    assert os.environ["UNIGROK_TEST_SAFE_ENV"] == "loaded"

    link = tmp_path / "linked.env"
    link.symlink_to(safe)
    with pytest.raises((OSError, RuntimeError)):
        cli._load_trusted_env(link)

    directory = tmp_path / "directory.env"
    directory.mkdir()
    with pytest.raises((OSError, RuntimeError)):
        cli._load_trusted_env(directory)


def test_trusted_env_rejects_wrong_owner(monkeypatch, tmp_path: Path):
    path = tmp_path / "owned.env"
    path.write_text("XAI_API_KEY=not-loaded\n", encoding="utf-8")
    path.chmod(0o600)
    real_uid = path.stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    with pytest.raises(RuntimeError, match="wrong owner"):
        cli._load_trusted_env(path)


def test_main_dispatches_rag_subcommand_without_starting_server(monkeypatch):
    import src.rag as rag_module

    called = {}

    def fake_rag_cli(args, stream=None, store=None):
        called["args"] = list(args)
        return 0

    monkeypatch.setattr(rag_module, "rag_cli", fake_rag_cli)
    assert cli.main(["rag", "status"]) == 0
    assert called["args"] == ["status"]
