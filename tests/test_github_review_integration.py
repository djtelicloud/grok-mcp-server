import asyncio
import importlib.util
import json
from pathlib import Path

import pytest


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
    provenance = {
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "evidence_sha256": "c" * 64,
    }
    body = module._format_comment(
        {
            "review": "## Verdict\nLooks safe.",
            "model": "grok-4.5",
            "plane": "CLI",
            "route": "agentic",
            "cost_usd": 0,
        },
        provenance=provenance,
        run_url="https://github.com/djtelicloud/grok-mcp-server/actions/runs/123",
    )

    assert body.startswith(module.MARKER)
    assert "@grok review for Codex" in body
    assert f"Reviewed head: `{provenance['head_sha']}`" in body
    assert f"Evidence: `sha256:{provenance['evidence_sha256']}`" in body
    assert "actions/runs/123" in body
    assert "Codex remains the sole landing and merge authority" in body


def test_github_review_evidence_digest_is_commit_and_content_bound():
    module = _load_script()
    pr = {
        "title": "Bound review",
        "head": {"sha": "a" * 40},
        "base": {"sha": "b" * 40},
    }
    first = module._evidence_provenance(
        repository="djtelicloud/grok-mcp-server",
        number=7,
        pr=pr,
        diff="diff --git a/a b/a",
        discussion="approved",
    )
    same = module._evidence_provenance(
        repository="djtelicloud/grok-mcp-server",
        number=7,
        pr=pr,
        diff="diff --git a/a b/a",
        discussion="approved",
    )
    changed = module._evidence_provenance(
        repository="djtelicloud/grok-mcp-server",
        number=7,
        pr=pr,
        diff="diff --git a/a b/a\n+new",
        discussion="approved",
    )

    assert first == same
    assert first["head_sha"] == "a" * 40
    assert first["base_sha"] == "b" * 40
    assert len(first["evidence_sha256"]) == 64
    assert first["evidence_sha256"] != changed["evidence_sha256"]


def test_github_review_rejects_head_change_before_comment(tmp_path, monkeypatch):
    module = _load_script()
    head = "a" * 40
    changed_head = "c" * 40
    base = "b" * 40
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 7,
                    "base": {"sha": base},
                    "head": {"sha": head},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "djtelicloud/grok-mcp-server")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.delenv("INPUT_PR_NUMBER", raising=False)
    monkeypatch.delenv("EXPECTED_BASE_SHA", raising=False)
    monkeypatch.delenv("EXPECTED_HEAD_SHA", raising=False)
    pull_reads = iter(
        [
            {"title": "PR", "head": {"sha": head}, "base": {"sha": base}},
            {
                "title": "PR",
                "head": {"sha": changed_head},
                "base": {"sha": base},
            },
        ]
    )

    def fake_github_request(url, _token, *, accept="application/vnd.github+json", **_kwargs):
        if "/compare/" in url and accept.endswith(".diff"):
            assert url.endswith(f"/compare/{base}...{head}")
            return "diff --git a/a b/a"
        if url.endswith("/reviews?per_page=100"):
            return []
        if url.endswith("/pulls/7"):
            return next(pull_reads)
        raise AssertionError(f"unexpected request: {url}")

    async def fake_unigrok(_arguments):
        return {
            "review": "Looks safe.",
            "model": "grok",
            "plane": "API",
            "route": "fast",
            "cost_usd": 0,
        }

    monkeypatch.setattr(module, "_github_request", fake_github_request)
    monkeypatch.setattr(module, "_call_unigrok", fake_unigrok)
    monkeypatch.setattr(
        module,
        "_upsert_comment",
        lambda *_args, **_kwargs: pytest.fail("stale review must not be published"),
    )

    with pytest.raises(RuntimeError, match="head changed"):
        asyncio.run(module.main())


def test_github_review_rejects_already_stale_event_head(tmp_path, monkeypatch):
    module = _load_script()
    event_head = "a" * 40
    current_head = "c" * 40
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 9,
                    "head": {"sha": event_head},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "djtelicloud/grok-mcp-server")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.delenv("INPUT_PR_NUMBER", raising=False)
    monkeypatch.delenv("EXPECTED_BASE_SHA", raising=False)
    monkeypatch.delenv("EXPECTED_HEAD_SHA", raising=False)

    def fake_github_request(url, _token, **_kwargs):
        assert url.endswith("/pulls/9")
        return {
            "title": "PR",
            "head": {"sha": current_head},
            "base": {"sha": "b" * 40},
        }

    async def fail_unigrok(_arguments):
        pytest.fail("stale event must be rejected before calling Grok")

    monkeypatch.setattr(module, "_github_request", fake_github_request)
    monkeypatch.setattr(module, "_call_unigrok", fail_unigrok)

    with pytest.raises(RuntimeError, match="no longer matches PR head"):
        asyncio.run(module.main())


def test_github_review_uses_immutable_compare_for_a_b_a_race(tmp_path, monkeypatch):
    module = _load_script()
    head = "a" * 40
    base = "b" * 40
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 11,
                    "base": {"sha": base},
                    "head": {"sha": head},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "djtelicloud/grok-mcp-server")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.delenv("INPUT_PR_NUMBER", raising=False)
    monkeypatch.delenv("EXPECTED_BASE_SHA", raising=False)
    monkeypatch.delenv("EXPECTED_HEAD_SHA", raising=False)
    pr = {"title": "PR", "head": {"sha": head}, "base": {"sha": base}}
    pull_reads = iter([pr, pr])
    requested_urls = []
    grok_arguments = {}
    comments = []

    def fake_github_request(url, _token, *, accept="application/vnd.github+json", **_kwargs):
        requested_urls.append((url, accept))
        if url.endswith("/pulls/11") and not accept.endswith(".diff"):
            return next(pull_reads)
        if url.endswith(f"/compare/{base}...{head}") and accept.endswith(".diff"):
            return "immutable diff for base and head"
        if url.endswith("/pulls/11/reviews?per_page=100"):
            return []
        if url.endswith("/pulls/11") and accept.endswith(".diff"):
            pytest.fail("mutable PR diff endpoint must never be used")
        raise AssertionError(f"unexpected request: {url}")

    async def fake_unigrok(arguments):
        grok_arguments.update(arguments)
        return {
            "review": "Reviewed immutable evidence.",
            "model": "grok",
            "plane": "API",
            "route": "fast",
            "cost_usd": 0,
        }

    monkeypatch.setattr(module, "_github_request", fake_github_request)
    monkeypatch.setattr(module, "_call_unigrok", fake_unigrok)
    monkeypatch.setattr(
        module,
        "_upsert_comment",
        lambda _api, _repository, _number, _token, body: comments.append(body),
    )

    asyncio.run(module.main())

    assert grok_arguments["diff"] == "immutable diff for base and head"
    assert f"/compare/{base}...{head}" in "\n".join(
        url for url, _accept in requested_urls
    )
    assert len(comments) == 1
    assert f"Reviewed head: `{head}` · Base: `{base}`" in comments[0]


def test_github_review_rejects_base_change_before_comment(tmp_path, monkeypatch):
    module = _load_script()
    head = "a" * 40
    base = "b" * 40
    changed_base = "d" * 40
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 12,
                    "base": {"sha": base},
                    "head": {"sha": head},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "djtelicloud/grok-mcp-server")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.delenv("INPUT_PR_NUMBER", raising=False)
    monkeypatch.delenv("EXPECTED_BASE_SHA", raising=False)
    monkeypatch.delenv("EXPECTED_HEAD_SHA", raising=False)
    pull_reads = iter(
        [
            {"title": "PR", "head": {"sha": head}, "base": {"sha": base}},
            {
                "title": "PR",
                "head": {"sha": head},
                "base": {"sha": changed_base},
            },
        ]
    )

    def fake_github_request(url, _token, *, accept="application/vnd.github+json", **_kwargs):
        if url.endswith("/pulls/12"):
            return next(pull_reads)
        if url.endswith(f"/compare/{base}...{head}") and accept.endswith(".diff"):
            return "immutable diff"
        if url.endswith("/pulls/12/reviews?per_page=100"):
            return []
        raise AssertionError(f"unexpected request: {url}")

    async def fake_unigrok(_arguments):
        return {
            "review": "Review",
            "model": "grok",
            "plane": "API",
            "route": "fast",
            "cost_usd": 0,
        }

    monkeypatch.setattr(module, "_github_request", fake_github_request)
    monkeypatch.setattr(module, "_call_unigrok", fake_unigrok)
    monkeypatch.setattr(
        module,
        "_upsert_comment",
        lambda *_args, **_kwargs: pytest.fail("stale base must not be published"),
    )

    with pytest.raises(RuntimeError, match="base/head changed"):
        asyncio.run(module.main())


def test_github_review_workflow_is_on_demand_and_never_checks_out_pr_code():
    workflow = (ROOT / ".github" / "workflows" / "grok-review.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" not in workflow
    assert "issue_comment:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "pull-requests: write" in workflow
    assert "issues: write" not in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "fromJSON(vars.UNIGROK_REVIEW_RUNNER_JSON" in workflow
    assert '["self-hosted","unigrok-review"]' in workflow
    assert "persist-credentials: false" in workflow
    assert "ref: ${{ github.event.pull_request.head" not in workflow
    assert "EXPECTED_BASE_SHA:" not in workflow
    assert "EXPECTED_HEAD_SHA:" not in workflow
    assert "vars.UNIGROK_REVIEW_MCP_URL" in workflow
    assert "vars.UNIGROK_REVIEW_PLANE || 'cli'" in workflow
    assert "UNIGROK_REVIEW_EXTERNALS" not in workflow
    assert "github.event.pull_request.author_association" not in workflow
    assert "github.event.comment.author_association" in workflow
    assert '[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]' in workflow


def test_chatgpt_github_operator_guide_keeps_credentials_separate():
    guide = (ROOT / "docs" / "chatgpt-github-app.md").read_text(encoding="utf-8")

    assert "Secure MCP Tunnel" in guide
    assert "Never use `XAI_API_KEY` as this token" in guide
    assert "never checks out, builds, imports, or runs code" in guide
    assert "Codex remains the only Git" in guide
    assert "immutable `base...head` commit" in guide
    assert "There is no switch that permits outside contributors" in guide
    assert "bootstrap binding are implemented" in guide
    assert "not live GitHub OAuth" in guide


def test_cloud_governance_contract_marks_target_features_not_live():
    adr = (
        ROOT / "docs" / "adr" / "0001-cloud-control-plane-governance.md"
    ).read_text(encoding="utf-8")
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")

    assert "GitHub sign-in plus repository authorization" in adr
    assert "successful GitHub OAuth login is **not** contributor authorization" in adr
    assert "GitHub mutation broker | Not implemented" in adr
    assert "not cryptographically signed" in adr
    assert "protected `origin/main`" in adr
    assert "Codex remains the decision authority" in adr
    assert "The user is not expected to perform routine Git" in adr
    assert "GitHub's CODEOWNERS syntax cannot name Codex" in adr
    assert "* @djtelicloud" in codeowners
    assert "/.github/ @djtelicloud" in codeowners
    assert "/scripts/land.py @djtelicloud" in codeowners


def test_ci_validates_the_provisioned_project_site():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "project-site:" in workflow
    assert "name: Project Site" in workflow
    assert "npm run test:deployment" in workflow
    assert "site-template:" not in workflow


def test_readme_describes_bound_site_and_bootstrap_authorization_truthfully():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())

    assert "bound to the existing project" in normalized
    assert "It is not an idless installer template" in normalized
    assert "**not** GitHub OAuth or a live collaborator lookup" in normalized
    assert "live GitHub verification remains pending" in normalized
