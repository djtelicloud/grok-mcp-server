import json
import re
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
    assert (
        f"v{__version__} Control Center" in index
        or f"Gateway Console v{__version__}" in index
        or f'Console <span class="version-badge">v{__version__}</span>' in index
    )
    assert f"## [{__version__}]" in changelog
    assert plugin["version"] == __version__


def test_public_install_warns_about_unrelated_pypi_distribution():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"(?m)^> \[!WARNING\]\n((?:^>.*(?:\n|$))+)", readme)
    warnings = [
        " ".join(line.removeprefix("> ").strip() for line in block.splitlines()).lower()
        for block in blocks
    ]

    assert any(
        "not published on pypi" in warning
        and "pip install mcp-grok" in warning
        and "unrelated project" in warning
        for warning in warnings
    )


def test_dependabot_covers_every_shipped_dependency_surface():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^\s{2}-\s+", config)[1:]
    configured: set[tuple[str, str]] = set()
    for block in blocks:
        ecosystem_match = re.search(
            r"(?m)^package-ecosystem:\s*['\"]?([^'\"\s]+)['\"]?\s*$",
            block,
        )
        directory_match = re.search(
            r"(?m)^\s*directory:\s*['\"]?([^'\"\s]+)['\"]?\s*$",
            block,
        )
        assert ecosystem_match is not None
        assert directory_match is not None
        configured.add((ecosystem_match.group(1), directory_match.group(1)))

    assert configured == {
        ("uv", "/"),
        ("npm", "/sites/unigrok-control-center"),
        ("github-actions", "/"),
        ("docker", "/"),
        ("docker", "/sites/unigrok-control-center"),
    }


def test_public_runtime_files_do_not_embed_a_developer_home_path():
    paths = [
        ROOT / "docker-compose.yml",
        ROOT / "docs" / "ide-setup.md",
        ROOT / "architecture.md",
        ROOT / "src" / "tools" / "system.py",
    ]
    for directory in (
        ROOT / ".agents",
        ROOT / ".claude",
        ROOT / ".codex",
        ROOT / ".gemini",
    ):
        paths.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix != ".pyc"
            and "__pycache__" not in path.parts
            and "worktrees" not in path.relative_to(ROOT).parts
        )
    private_home = "/Users/" + "djtelicloud"

    for path in paths:
        assert private_home not in path.read_text(encoding="utf-8"), path


def test_wheel_configuration_includes_runtime_assets():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert included["mcp_ui"] == "mcp_ui"
    assert included["docs/okf"] == "docs/okf"
    assert included[".grok/hyperparams"] == ".grok/hyperparams"
    assert included[".grok/prompts"] == ".grok/prompts"
    assert ".grok" not in included
    assert included["example.env"] == "example.env"


def test_local_provider_credentials_are_ignored_and_not_force_included():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for path in (
        ".codex/auth.json",
        ".gemini/config/",
        ".gemini/settings.local.json",
        ".claude/.credentials.json",
        ".grok/auth.json",
        ".gcloud/",
        ".google/",
        "secrets/",
    ):
        assert path in gitignore

    assert ".grok/**" in dockerignore
    assert "!.grok/prompts/**" in dockerignore
    assert "!.grok/hyperparams/**" in dockerignore
    assert "COPY .grok/ ./.grok/" not in dockerfile
    assert "COPY .grok/prompts/ ./.grok/prompts/" in dockerfile
    assert "COPY .grok/hyperparams/ ./.grok/hyperparams/" in dockerfile

    gemini_config = json.loads((ROOT / ".gemini" / "config.json").read_text())
    assert gemini_config == {
        "userSettings": {
            "browserJsExecutionPolicy": "BROWSER_JS_EXECUTION_POLICY_TURBO",
            "useAiCredits": True,
            "verboseAgentChat": True,
        }
    }
    gemini_guidance = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    assert "must never be replaced" in gemini_guidance
    assert "standard ADC discovery" in gemini_guidance


def test_forge_execution_tools_are_not_core_runtime_dependencies():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = set(metadata["project"]["dependencies"])
    forge = set(metadata["project"]["optional-dependencies"]["forge"])
    dev = set(metadata["dependency-groups"]["dev"])

    for name in ("coverage", "pytest", "ruff"):
        assert not any(dep.startswith(name) for dep in core)
        assert any(dep.startswith(name) for dep in forge)
        assert any(dep.startswith(name) for dep in dev)


def test_sdist_configuration_excludes_generated_dependency_trees():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(metadata["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"])

    assert "/sites/**/node_modules" in excluded
    assert "/sites/**/.sites-runtime" in excluded
    assert "/sites/**/.next" in excluded
    assert "/sites/**/.wrangler" in excluded
    assert "/**/__pycache__" in excluded


def test_public_setup_surfaces_use_the_grok_phoneword_endpoint():
    paths = [
        ROOT / "README.md",
        ROOT / "docs" / "ide-setup.md",
        ROOT / "docs" / "okf" / "faq.md",
        ROOT / "src" / "cli.py",
        ROOT / ".mcp.json",
        ROOT / "skills" / "using-unigrok" / "SKILL.md",
        ROOT / ".github" / "skills" / "using-unigrok" / "SKILL.md",
        ROOT / ".agents" / "skills" / "using-unigrok" / "SKILL.md",
        ROOT / ".claude" / "skills" / "using-unigrok" / "SKILL.md",
        ROOT / ".claude" / "settings.json",
        ROOT / ".agents" / "AGENTS.md",
        ROOT / ".agents" / "skills" / "uni-grok-mcp" / "SKILL.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "http://localhost:4765" in text, path
        assert "http://localhost:8080" not in text, path


def test_session_rehydrate_skills_use_project_qualified_continuity():
    agent_skill = (ROOT / ".agents" / "skills" / "session-rehydrate" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    claude_skill = (ROOT / ".claude" / "skills" / "session-rehydrate" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    shared_rules = (ROOT / ".agents" / "AGENTS.md").read_text(encoding="utf-8")

    for text in (agent_skill, claude_skill, shared_rules):
        assert "../unigrok-intelligence/" in text
        assert "`unigrok-intelligence/" not in text
    for text in (agent_skill, claude_skill):
        assert "djtelicloud-grok-mcp-server:ops:YYYY-MM-DD" in text
        assert "unigrok:ops:YYYY-MM-DD" not in text
    assert "Root `CLAUDE.md` if present" in claude_skill


def test_agent_statuses_lead_with_plain_task_titles() -> None:
    shared_rules = (ROOT / ".agents" / "AGENTS.md").read_text(encoding="utf-8")
    agent_skill = (
        ROOT / ".agents" / "skills" / "session-rehydrate" / "SKILL.md"
    ).read_text(encoding="utf-8")
    claude_skill = (
        ROOT / ".claude" / "skills" / "session-rehydrate" / "SKILL.md"
    ).read_text(encoding="utf-8")
    gemini_rules = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")

    assert "Task titles, not ticket numbers" in shared_rules
    assert "never the lead" in shared_rules
    for guidance in (agent_skill, claude_skill, gemini_rules):
        assert "plain task title" in guidance
        assert "Never lead with PR" in guidance


def test_agent_human_radio_stays_silent_and_consistent() -> None:
    shared_rules = (ROOT / ".agents" / "AGENTS.md").read_text(encoding="utf-8")
    agent_skill = (
        ROOT / ".agents" / "skills" / "session-rehydrate" / "SKILL.md"
    ).read_text(encoding="utf-8")
    claude_skill = (
        ROOT / ".claude" / "skills" / "session-rehydrate" / "SKILL.md"
    ).read_text(encoding="utf-8")
    gemini_rules = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    public_pack = (
        ROOT
        / "docs"
        / "public-intelligence"
        / "packs"
        / "v0-human-radio-and-cloud-boundary.md"
    ).read_text(encoding="utf-8")

    assert "Chat pollution is a product bug" in shared_rules
    assert "meaningful state" in shared_rules
    for skill in (agent_skill, claude_skill):
        assert "No diffs, patches, or tool dumps in chat" in skill
        assert "required Rehydrated block below" in skill
    assert "diffs, tool dumps, progress essays" in gemini_rules
    assert "Not live" in public_pack
    assert "continuously monitored work" in public_pack
    assert "Root `CLAUDE.md` if present" in claude_skill


def test_disposable_scratchpad_cleanup_is_consistent():
    """Own finished scratchpads may be removed; peer/main deletion stays forbidden."""
    shared = (ROOT / ".agents" / "AGENTS.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "uni-grok-mcp" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    root_agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    gemini = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    agent_rehydrate = (
        ROOT / ".agents" / "skills" / "session-rehydrate" / "SKILL.md"
    ).read_text(encoding="utf-8")
    claude_rehydrate = (
        ROOT / ".claude" / "skills" / "session-rehydrate" / "SKILL.md"
    ).read_text(encoding="utf-8")
    claude_root = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    for text in (shared, skill, root_agents):
        normalized = " ".join(text.split())
        assert "finished disposable scratchpad" in normalized
        # Each guidance surface must protect primary main (not only shared).
        assert (
            "primary main checkout" in normalized
            or "Never delete another agent" in normalized
            or "never delete peers" in normalized
        ), "expected primary-main / peer protection"
    assert "Never delete another agent" in shared or "Never remove peers" in shared
    assert "primary main" in shared or "primary main checkout" in shared
    assert "Cursor Automations" in shared
    assert "Single-agent only" in shared
    assert "After Live, abandonment, or a new task" in gemini
    assert "After Ready for supervisor" not in gemini
    assert "Ready / Not ready / Live / Not live / Blocked" in gemini
    for rehydrate in (agent_rehydrate, claude_rehydrate):
        assert "If this task is done (Live, abandoned, or new task assigned)" in rehydrate
    assert "own finished disposable scratchpad" in " ".join(claude_root.split())
    assert "Never remove peer worktrees or the primary main checkout" in " ".join(
        claude_root.split()
    )
    assert "or remove worktrees. A" not in claude_root
    # Old absolute ban must not remain as a hard stop without the exception.
    assert "or delete worktrees unless they are explicitly acting" not in shared
    assert "Never remove task worktrees after landing" not in skill


def test_public_docs_surfaces_exclude_github_wiki_as_product():
    """Public knowledge is README + OKF; GitHub Wiki is not a second tree."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    freeze = (ROOT / "docs" / "design" / "public-vs-insider-surfaces.md").read_text(
        encoding="utf-8"
    )

    assert "## 8. Where docs live" in readme
    assert "https://grokmcp.org/docs/okf/index.md" in readme
    assert "There is **no** separate GitHub Wiki product surface" in readme
    assert "GitHub Wiki is not a product surface" in contributing
    assert "Do not hand-edit it as source of" in contributing
    assert "auto-publish full OKF into `.wiki.git`" in contributing
    assert "GitHub Wiki as second docs tree" in freeze
    assert "Forbidden** — public knowledge is README + OKF only" in freeze


def test_agent_guidance_preserves_workspace_and_credential_boundaries():
    using_unigrok = (ROOT / "skills" / "using-unigrok" / "SKILL.md").read_text(encoding="utf-8")
    claude = (ROOT / ".claude" / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    gemini = (ROOT / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")

    assert 'fallback_policy="same_plane"' in using_unigrok
    assert "workspace-neutral" in using_unigrok
    assert "workspace-neutral" in gemini

    for guidance in (using_unigrok, claude):
        description = guidance.split("---", maxsplit=2)[1].lower()
        normalized = " ".join(guidance.split())
        assert "deferred" not in description
        assert "imagine" not in description
        assert "video" not in description
        assert "project-qualified" in normalized
        assert "workspace_label" in normalized
        assert "descriptive" in normalized
        assert "does not isolate" in normalized
        assert "collide across repositories" in normalized
        assert "server-derived principal" in normalized
        assert "http:anon" in normalized
        assert "shared `http:anon` principal" in normalized
        assert "caller-controlled" in normalized
        assert "no cross-user isolation without configured auth" in normalized
        assert "caching hits" in normalized
        assert "CLI provider cost remains unavailable" in normalized


def test_copilot_project_skill_uses_a_supported_discovery_path():
    skill_path = ROOT / ".github" / "skills" / "using-unigrok" / "SKILL.md"
    assert skill_path.is_file()
    assert not (ROOT / ".copilot" / "skills" / "using-unigrok" / "SKILL.md").exists()
    assert "not a default project discovery path" in skill_path.read_text(encoding="utf-8")


def test_shared_agent_project_skill_tracks_the_canonical_skill():
    canonical = (ROOT / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    shared = (ROOT / ".agents" / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert shared == canonical


def test_claude_project_skill_and_permissions_use_supported_discovery_paths():
    skill_path = ROOT / ".claude" / "skills" / "using-unigrok" / "SKILL.md"
    claude_rules = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allowed = set(settings["permissions"]["allow"])

    assert skill_path.is_file()
    assert ".claude/skills/using-unigrok/SKILL.md" in claude_rules
    assert "mcp__unigrok__agent" in allowed
    assert "mcp__unigrok__grok_mcp_status" in allowed
    assert "mcp__unigrok__grok_mcp_discover_self" in allowed
    assert not any(entry.startswith("mcp__grok__") for entry in allowed)
    assert "Bash(./scripts/land-status)" in allowed
    assert "Bash(./scripts/land-status:*)" not in allowed

    skill = skill_path.read_text(encoding="utf-8")
    assert "mcp__grok__agent" in skill
    assert "user-owned `grok` alias" in skill
    assert "verify that it" in skill


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
