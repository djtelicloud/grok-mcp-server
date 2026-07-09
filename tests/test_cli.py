import io
from pathlib import Path

import src.cli as cli
from src.cli import init_project


def test_init_project_copies_example_env_and_prints_ide_configs(tmp_path: Path):
    (tmp_path / "example.env").write_text("XAI_API_KEY=your_xai_api_key_here\n", encoding="utf-8")
    stream = io.StringIO()

    code = init_project(tmp_path, stream)

    assert code == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "XAI_API_KEY=your_xai_api_key_here\n"
    out = stream.getvalue()
    assert "Created" in out
    assert "VS Code" in out
    assert "Claude Desktop" in out
    assert "Claude Code" in out
    assert "Codex" in out
    assert "http://localhost:8080/mcp" in out


def test_init_project_leaves_existing_env_unchanged(tmp_path: Path):
    (tmp_path / "example.env").write_text("XAI_API_KEY=template\n", encoding="utf-8")
    (tmp_path / ".env").write_text("XAI_API_KEY=real\n", encoding="utf-8")
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
    assert "packaged environment template" in stream.getvalue()
