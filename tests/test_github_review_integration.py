import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "github-grok-review.py"
    spec = importlib.util.spec_from_file_location("github_grok_review", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_github_review_comment_is_advisory_and_idempotent_marker():
    module = _load_script()
    body = module._format_comment(
        {
            "review": "## Verdict\nLooks safe.",
            "model": "grok-4.5",
            "plane": "CLI",
            "route": "agentic",
            "cost_usd": 0,
        }
    )

    assert body.startswith(module.MARKER)
    assert "@grok review for Codex" in body
    assert "Codex remains the sole landing and merge authority" in body


def test_github_review_workflow_never_checks_out_pr_code():
    workflow = (ROOT / ".github" / "workflows" / "grok-review.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "runs-on: [self-hosted, unigrok-review]" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.pull_request.head" not in workflow
    assert "UNIGROK_REVIEW_PLANE: cli" in workflow


def test_chatgpt_github_operator_guide_keeps_credentials_separate():
    guide = (ROOT / "docs" / "chatgpt-github-app.md").read_text(encoding="utf-8")

    assert "Secure MCP Tunnel" in guide
    assert "Never use `XAI_API_KEY` as this token" in guide
    assert "never checks out, builds, imports, or runs code" in guide
    assert "Codex remains the only Git" in guide
