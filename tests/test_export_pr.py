import pytest
from unittest.mock import patch, AsyncMock
import hashlib
from src.tools.swarm import export_swarm_narrow_pr

@pytest.fixture
def workspace(tmp_path):
    # Setup mock workspace
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    target = src_dir / "foo.py"
    target.write_text("def complex_func():\n    return 1\n")
    
    with patch("src.tools.swarm.PathResolver.get_workspace_root", return_value=tmp_path):
        yield tmp_path

@pytest.mark.asyncio
async def test_export_swarm_narrow_pr(workspace):
    target = workspace / "src" / "foo.py"
    live = target.read_bytes()
    base_hash = hashlib.sha256(live).hexdigest()

    mock_task = {
        "id": "task-123",
        "target_path": "src/foo.py",
        "base_file_hash": base_hash,
        "focus_node": "function:complex_func"
    }

    mock_candidate = {
        "id": "cand-456",
        "task_id": "task-123",
        "code": "def complex_func():\n    return 2\n",
        "byte_start": 0,
        "byte_end": len(live),
        "feasible": True,
        "latency_ms": 150,
        "pareto_rank": 0,
        "crowding": 1.0,
        "mutator": "optimizer"
    }

    mock_store = AsyncMock()
    mock_store.get_swarm_task.return_value = mock_task
    mock_store.list_swarm_candidates.return_value = [mock_candidate]
    
    with patch("src.tools.swarm.store", mock_store):
        res = await export_swarm_narrow_pr("task-123")
        assert res["format"] == "unigrok-swarm-narrow-pr-v1"
        assert res["task_id"] == "task-123"
        assert res["candidate_id"] == "cand-456"
        assert res["hash_matches"] is True
        assert res["verification"]["latency_ms"] == 150
        assert "return 2" in res["diff"]
        assert "return 1" in res["diff"]

@pytest.mark.asyncio
async def test_export_swarm_narrow_pr_honors_primary_goal(workspace):
    target = workspace / "src" / "foo.py"
    live = target.read_bytes()
    base_hash = hashlib.sha256(live).hexdigest()

    mock_task = {
        "id": "task-123",
        "target_path": "src/foo.py",
        "base_file_hash": base_hash,
        "focus_node": "function:complex_func",
        "primary_goal": "memory"
    }

    mock_fast_candidate = {
        "id": "cand-fast",
        "task_id": "task-123",
        "code": "def complex_func():\n    return 2\n",
        "byte_start": 0,
        "byte_end": len(live),
        "feasible": True,
        "latency_ms": 10,
        "peak_mem_bytes": 1000,
        "pareto_rank": 0,
        "mutator": "optimizer"
    }

    mock_memory_candidate = {
        "id": "cand-memory",
        "task_id": "task-123",
        "code": "def complex_func():\n    return 3\n",
        "byte_start": 0,
        "byte_end": len(live),
        "feasible": True,
        "latency_ms": 50,
        "peak_mem_bytes": 100,
        "pareto_rank": 0,
        "mutator": "optimizer"
    }

    mock_store = AsyncMock()
    mock_store.get_swarm_task.return_value = mock_task
    mock_store.list_swarm_candidates.return_value = [mock_fast_candidate, mock_memory_candidate]
    
    with patch("src.tools.swarm.store", mock_store):
        res = await export_swarm_narrow_pr("task-123")
        assert res["primary_goal"] == "memory"
        assert res["candidate_id"] == "cand-memory"
        assert res["verification"]["peak_mem_bytes"] == 100

