# tests/test_consistency.py
import pytest
from unittest.mock import patch, AsyncMock
from src.tools.consistency import architecture_consistency_sweep

@pytest.fixture
def workspace(tmp_path):
    # Mock workspace with some rules and targets
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "rule.md").write_text("Rule 1: Always do X.")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def do_x(): pass")

    with patch("src.tools.consistency.PathResolver.get_workspace_root", return_value=tmp_path):
        yield tmp_path

@pytest.mark.asyncio
async def test_sweep_validates_inputs(workspace):
    res = await architecture_consistency_sweep([], [])
    assert "Input Validation Error" in res.get("error", "")

@pytest.mark.asyncio
async def test_sweep_reads_files_and_calls_agent(workspace):
    mock_agent = AsyncMock(return_value='{"Consistency Score": "100/100"}')
    with patch("src.tools.consistency.agent", mock_agent):
        res = await architecture_consistency_sweep(
            target_paths=["src/app.py"],
            rules_paths=["docs/rule.md"],
            ctx=None
        )
        assert res["status"] == "success"
        assert res["report"]["Consistency Score"] == "100/100"

        # Check prompt contained the file contents
        mock_agent.assert_called_once()
        kwargs = mock_agent.call_args.kwargs
        workspace_context = kwargs.get("workspace_context")
        assert "Rule 1: Always do X." in workspace_context
        assert "def do_x(): pass" in workspace_context

@pytest.mark.asyncio
async def test_sweep_handles_missing_files(workspace):
    res = await architecture_consistency_sweep(
        target_paths=["src/missing.py"],
        rules_paths=["docs/rule.md"],
        ctx=None
    )
    assert "Failed to read target file" in res.get("error", "")
