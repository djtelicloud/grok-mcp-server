import tomllib
from pathlib import Path

from src.version import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_aligned_across_package_runtime_and_ui():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    index = (ROOT / "mcp_ui" / "index.html").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert metadata["project"]["version"] == __version__ == "0.5.0"
    assert f"v{__version__} Control Center" in index
    assert f"## [{__version__}]" in changelog


def test_public_runtime_files_do_not_embed_a_developer_home_path():
    paths = [
        ROOT / "docker-compose.yml",
        ROOT / "docs" / "ide-setup.md",
        ROOT / "architecture.md",
        ROOT / "src" / "tools" / "system.py",
    ]
    for directory in (ROOT / ".agents", ROOT / ".codex", ROOT / ".gemini"):
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    private_home = "/Users/" + "djtelicloud"

    for path in paths:
        assert private_home not in path.read_text(encoding="utf-8"), path


def test_wheel_configuration_includes_runtime_assets():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert included["mcp_ui"] == "mcp_ui"
    assert included["docs/okf"] == "docs/okf"
    assert included[".grok"] == ".grok"
    assert included["example.env"] == "example.env"


def test_public_setup_surfaces_use_the_grok_phoneword_endpoint():
    paths = [
        ROOT / "README.md",
        ROOT / "docs" / "ide-setup.md",
        ROOT / "docs" / "okf" / "faq.md",
        ROOT / "src" / "cli.py",
        ROOT / ".mcp.json",
        ROOT / "skills" / "using-unigrok" / "SKILL.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "http://localhost:4765" in text, path
        assert "http://localhost:8080" not in text, path
