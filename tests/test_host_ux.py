"""Host UX helpers: cream layout, poll contract, soft-continue, pure-QA short-circuit."""

from unigrok_public import host_ux


def test_cream_first_layout_leads_with_code_fence() -> None:
    body = (
        "Here is the plan in prose first.\n\n"
        "```python\ndef process(nums):\n    return sum(set(nums))\n```\n\n"
        "More rationale after."
    )
    out = host_ux.cream_first_layout(body)
    assert out.startswith("```python")
    assert "More rationale" in out
    assert "---" in out


def test_cream_first_layout_artifact_section() -> None:
    body = (
        "Thinking notes...\n\n"
        "## ARTIFACT\n```python\nx = 1\n```\n\n"
        "## SELF_CONF\n90"
    )
    out = host_ux.cream_first_layout(body)
    assert "ARTIFACT" in out[:40] or out.strip().startswith("## ARTIFACT")


def test_poll_contract_shape() -> None:
    c = host_ux.poll_contract("abc123", kind="agent", wait_seconds=16)
    assert c["tool"] == "agent_result"
    assert c["job_id"] == "abc123"
    assert c["wait_seconds"] == 16
    assert "agent_result" in c["host_instructions"]
    env = host_ux.attach_poll_contract(
        {"status": "pending", "text": "working"}, "abc123", kind="chat"
    )
    assert env["poll"]["job_id"] == "abc123"
    assert env["poll_contract"]["max_polls_hint"] >= 1


def test_looks_like_pure_qa() -> None:
    assert host_ux.looks_like_pure_qa("What is UniGrok?")
    assert host_ux.looks_like_pure_qa("Explain how continue_token works")
    assert not host_ux.looks_like_pure_qa(
        "Implement process() in solution.py with unit tests"
    )
    assert not host_ux.looks_like_pure_qa("fix the bug in auth and run tests")
    assert not host_ux.looks_like_pure_qa("x" * 600)


def test_should_soft_continue() -> None:
    assert host_ux.should_soft_continue(
        {"text": "partial draft", "stop_reason": "max_turns"}
    )
    assert host_ux.should_soft_continue(
        {"text": "partial", "tool_budget_exhausted": True}
    )
    assert not host_ux.should_soft_continue({"text": "", "stop_reason": "max_turns"})
    assert not host_ux.should_soft_continue(
        {"text": "full answer", "stop_reason": "EndTurn"}
    )
    marked = host_ux.apply_soft_continue_markers(
        {"text": "draft", "stop_reason": "length"}
    )
    assert marked.get("soft_continue") is True
    assert marked.get("partial") is True
