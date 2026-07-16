from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / ".github" / "agent-identities.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_agent_attribution", ROOT / "scripts" / "check_agent_attribution.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


attribution = _load_module()


def _git(
    repo: Path,
    *args: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        input=input_text,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Human")
    _git(repo, "config", "user.email", "human@example.test")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit(
    repo: Path,
    message: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
) -> str:
    path = repo / "file.txt"
    path.write_text(path.read_text(encoding="utf-8") + message.splitlines()[0] + "\n")
    _git(repo, "add", "file.txt")
    env: dict[str, str] = {}
    if author_name is not None:
        env["GIT_AUTHOR_NAME"] = author_name
    if author_email is not None:
        env["GIT_AUTHOR_EMAIL"] = author_email
    _git(repo, "commit", "-F", "-", input_text=message, env=env)
    return _git(repo, "rev-parse", "HEAD")


def _errors(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    return attribution.validate_commit_range(
        repo,
        base_ref=base,
        head_ref=head,
        registry_path=REGISTRY,
    )


def test_repeated_canonical_assistance_and_review_trailers_pass(
    git_repo: tuple[Path, str],
) -> None:
    repo, base = git_repo
    _commit(
        repo,
        """credit all material agents

Agent-Assisted-By: OpenAI Codex | model=GPT-5 | model-source=user-reported | surface=Codex Desktop | role=implementation
Agent-Assisted-By: Google Gemini | model=Gemini 3.1 Pro | model-source=provider-session | surface=Antigravity | role=data-design
Agent-Reviewed-By: xAI Grok | model=grok-4.5 | model-source=runtime-receipt | surface=UniGrok CLI | role=advisory-review | evidence=receipt-sha256:0123456789abcdef
""",
    )

    assert _errors(repo, base) == []


def test_synthetic_ai_coauthor_is_rejected(git_repo: tuple[Path, str]) -> None:
    repo, base = git_repo
    _commit(
        repo,
        """synthetic coauthor

Agent-Assisted-By: Anthropic Claude | model=Fable 5 | model-source=user-reported | surface=Claude Code | role=implementation
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
""",
    )

    errors = _errors(repo, base)
    assert len(errors) == 1
    assert "not an exact verified GitHub identity" in errors[0]


def test_verified_copilot_bot_identity_is_allowed(git_repo: tuple[Path, str]) -> None:
    repo, base = git_repo
    _commit(
        repo,
        """verified bot coauthor

Agent-Assisted-By: GitHub Copilot | model=unverified | model-source=unverified | surface=GitHub coding agent | role=implementation
Co-authored-by: Copilot <198982749+Copilot@users.noreply.github.com>
""",
    )

    assert _errors(repo, base) == []


def test_wrong_copilot_email_is_not_treated_as_verified(
    git_repo: tuple[Path, str],
) -> None:
    repo, base = git_repo
    _commit(
        repo,
        """unverified bot coauthor

Agent-Assisted-By: GitHub Copilot | model=unverified | model-source=unverified | surface=VS Code | role=implementation
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
""",
    )

    assert "not an exact verified GitHub identity" in _errors(repo, base)[0]


def test_ai_branded_git_author_requires_verified_bot_identity(
    git_repo: tuple[Path, str],
) -> None:
    repo, base = git_repo
    _commit(
        repo,
        "synthetic author",
        author_name="Codex GPT-5",
        author_email="codex@openai.com",
    )

    assert "Git author is not an exact verified" in _errors(repo, base)[0]


def test_real_human_coauthor_remains_allowed(git_repo: tuple[Path, str]) -> None:
    repo, base = git_repo
    _commit(
        repo,
        """human coauthor

Co-authored-by: Real Person <real.person@example.test>
""",
    )

    assert _errors(repo, base) == []


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            "Codex | model=GPT-5 | model-source=user-reported | surface=Desktop | role=implementation",
            "unknown agent credit identity",
        ),
        (
            "OpenAI Codex <codex@openai.com> | model=GPT-5 | model-source=user-reported | surface=Desktop | role=implementation",
            "credit_name",
        ),
        (
            "OpenAI Codex | model=GPT-5 | model-source=unverified | surface=Desktop | role=implementation",
            "must be used together",
        ),
        (
            "OpenAI Codex | model=GPT-5 | model-source=user-reported | surface=Desktop | role=advisory-review",
            "not allowed",
        ),
        (
            "xAI Grok | model=grok-4.5 | model-source=runtime-receipt | surface=UniGrok CLI | role=research",
            "requires a bounded evidence reference",
        ),
    ],
)
def test_malformed_or_misleading_agent_credit_is_rejected(
    value: str, expected: str
) -> None:
    registry = attribution.load_registry(REGISTRY)
    with pytest.raises(attribution.AttributionError, match=expected):
        attribution.validate_agent_credit("Agent-Assisted-By", value, registry)


def test_legacy_invalid_trailer_before_merge_base_does_not_block(
    git_repo: tuple[Path, str],
) -> None:
    repo, _initial = git_repo
    legacy = _commit(
        repo,
        """legacy synthetic identity

Co-authored-by: Claude Fable 5 <noreply@anthropic.com>
""",
    )
    _commit(
        repo,
        """bounded new work

Agent-Assisted-By: OpenAI Codex | model=unverified | model-source=unverified | surface=Codex Desktop | role=testing
""",
    )

    assert _errors(repo, legacy) == []


def test_registry_and_visible_credits_cover_all_provider_products() -> None:
    registry = attribution.load_registry(REGISTRY)
    expected = {
        "Anthropic Claude",
        "Cursor",
        "Cursor Composer",
        "GitHub Copilot",
        "Google Gemini",
        "OpenAI Codex",
        "xAI Grok",
    }
    assert registry.credit_names == expected
    contributors = (ROOT / "CONTRIBUTORS.md").read_text(encoding="utf-8")
    assert all(name in contributors for name in expected)
