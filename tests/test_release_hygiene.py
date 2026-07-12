import json
import tomllib
from pathlib import Path

from src.version import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_aligned_across_package_runtime_and_ui():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    index = (ROOT / "mcp_ui" / "index.html").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    plugin = json.loads((ROOT / ".plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == __version__ == "0.6.0"
    assert f"v{__version__} Control Center" in index
    assert f"## [{__version__}]" in changelog
    assert plugin["version"] == __version__


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
        ROOT / ".agents" / "AGENTS.md",
        ROOT / ".agents" / "skills" / "uni-grok-mcp" / "SKILL.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "http://localhost:4765" in text, path
        assert "http://localhost:8080" not in text, path


def test_agent_guidance_preserves_workspace_and_credential_boundaries():
    using_unigrok = (ROOT / "skills" / "using-unigrok" / "SKILL.md").read_text(encoding="utf-8")
    gemini = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")

    assert 'fallback_policy="same_plane"' in using_unigrok
    assert "workspace-neutral" in using_unigrok
    assert "workspace-neutral" in gemini


def test_agent_rules_allow_draft_pr_submission_but_reserve_final_integration():
    shared_rules = (ROOT / ".agents" / "AGENTS.md").read_text(encoding="utf-8")
    claude_rules = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    gemini_rules = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    copilot_rules = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")

    assert "Codex Owns Final Integration" in shared_rules
    assert "open or update a draft pull request" in shared_rules
    assert "open or update a draft pull request" in claude_rules
    assert "open or update a draft pull request" in gemini_rules
    assert "open or update a draft pull request" in copilot_rules
    assert "GitHub Copilot" in shared_rules
    assert "GitHub Copilot" in claude_rules
    assert "Codex Integration Owner" in gemini_rules
    assert "scripts/land" in copilot_rules
