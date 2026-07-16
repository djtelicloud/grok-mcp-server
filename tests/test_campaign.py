import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock
from src.tools.swarm import plan_swarm_campaign

@pytest.fixture
def workspace(tmp_path):
    # Setup mock workspace
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "foo.py").write_text("def complex_func():\n" + "    for i in range(10):\n        if i % 2 == 0:\n            pass\n" * 5)
    (src_dir / "tiny.py").write_text("def trivial(): pass\n")
    
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_foo.py").write_text("def test_complex(): pass\n")
    
    with patch("src.tools.swarm.PathResolver.get_workspace_root", return_value=tmp_path):
        yield tmp_path

@pytest.mark.asyncio
async def test_plan_swarm_campaign(workspace):
    res = await plan_swarm_campaign(target_paths=["src"], test_roots=["tests"], max_targets=5)
    print(json.dumps(res, indent=2))
