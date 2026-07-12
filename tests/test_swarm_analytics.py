"""Non-executing deterministic analytics for pasted Swarm targets."""

import json

import pytest

from src.swarm.analytics import MAX_SOURCE_BYTES, analyze_python_source, analyze_python_source_full
from src.tools import swarm as swarm_tools


SOURCE = '''
import os
import json as js

def simple(value):
    return value + 1

class Worker:
    async def choose(self, items, flag=False):
        if flag and items:
            for item in items:
                if item:
                    return item
        return None

def outer(values):
    def inner(value):
        return value * 2
    return [inner(value) for value in values if value]
'''.lstrip()


def test_inventory_and_measured_metrics_are_stable():
    result = analyze_python_source(SOURCE)
    assert result["format"] == "unigrok-swarm-analytics-v1"
    assert result["parse_ok"] is True
    assert [item["focus_node"] for item in result["functions"]] == [
        "function:simple",
        "method:Worker.choose",
        "function:outer",
        "function:outer.inner",
    ]
    choose = result["functions"][1]
    assert choose["cyclomatic_complexity"] == 5
    assert choose["max_nesting"] == 3
    assert result["imports"] == ["json", "os"]
    assert result["dead_code"]["unused_imports"] == ["js", "os"]
    assert result["searchability"]["ready"] is False
    assert "missing_tests" in result["searchability"]["blockers"]


def test_parse_error_and_secret_warning_never_echo_secret():
    secret = "xai-abcdefgh12345678"
    result = analyze_python_source(f'KEY = "{secret}"\ndef nope(:\n')
    assert result["parse_ok"] is False
    assert result["secret_warning"] is True
    assert secret not in json.dumps(result)


def test_size_cap_is_byte_based():
    with pytest.raises(ValueError, match="256 KiB"):
        analyze_python_source("é" * (MAX_SOURCE_BYTES // 2 + 1))


@pytest.mark.asyncio
async def test_full_analysis_adds_only_ruff_aggregates():
    result = await analyze_python_source_full("import os\n\ndef f():\n    return missing\n")
    assert set(result["ruff"]) == {"available", "counts_by_code"}
    if result["ruff"]["available"]:
        assert result["ruff"]["counts_by_code"]["F821"] == 1


@pytest.mark.asyncio
async def test_tool_refuses_cloud_and_does_not_require_workspace(monkeypatch):
    monkeypatch.setattr(swarm_tools, "is_cloudrun_runtime", lambda: False)
    payload = json.loads(await swarm_tools.analyze_code_for_swarm("def f(x):\n    return x\n"))
    assert payload["functions"][0]["focus_node"] == "function:f"

    monkeypatch.setattr(swarm_tools, "is_cloudrun_runtime", lambda: True)
    refusal = json.loads(await swarm_tools.analyze_code_for_swarm("def f(): pass"))
    assert "Cloud Run" in refusal["error"]
