"""Executable JavaScript workflow test for needle-training-campaign.js.

The campaign orchestrator is JavaScript; its command construction, receipt
consumption, and cross-arm gating logic must be tested in its own language.
This wrapper runs tests/js/needle_workflow.test.cjs under node with mock
agents that execute the real ``evals.needle_gates`` CLI (mock fixture only —
nothing live, no providers, no credentials).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_TEST = REPO_ROOT / "tests" / "js" / "needle_workflow.test.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_needle_workflow_js():
    result = subprocess.run(
        ["node", str(JS_TEST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"JS workflow test failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "all needle workflow JS tests passed" in result.stdout
