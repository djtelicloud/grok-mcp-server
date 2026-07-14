import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_okf", ROOT / "scripts" / "generate_okf.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load_generator()


def test_get_keywords():
    assert generator.get_keywords("CamelCaseTest") == "camel, case, test"
    assert generator.get_keywords("snake_case_test") == "snake, case, test"
    assert generator.get_keywords("FAQDocumentError") == "faq, document, error"
    assert generator.get_keywords("Agent.run_async") == "agent, run, async"


def test_extracts_sync_async_classes_and_public_methods(tmp_path):
    py_file = tmp_path / "test_module.py"
    py_file.write_text(
        '''
class MyClass:
    """Class docstring."""

    async def run_async(self, value: str) -> bool:
        """Run asynchronously."""

    def _private(self):
        """Hidden method."""

def my_func(value: int = 1) -> str:
    """Func docstring."""

async def my_async_func(*, enabled: bool = True) -> None:
    """Async docstring."""
''',
        encoding="utf-8",
    )

    items = generator.extract_docs_from_file(py_file)

    assert [item["name"] for item in items] == [
        "MyClass",
        "MyClass.run_async",
        "my_func",
        "my_async_func",
    ]
    assert items[1]["signature"] == "async def MyClass.run_async(self, value: str) -> bool"
    assert items[2]["signature"] == "def my_func(value: int=1) -> str"
    assert items[3]["signature"] == "async def my_async_func(*, enabled: bool=True) -> None"


def test_parse_failure_is_fatal(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")

    with pytest.raises(generator.GenerationError, match="cannot parse"):
        generator.extract_docs_from_file(broken)


def test_render_is_deterministic_and_contains_async_public_tools():
    first = generator.render_api_reference()
    second = generator.render_api_reference()

    assert first == second
    assert "async def agent(" in first
    assert "async def generate_image(" in first
    assert "### Function: `agent`" in first
    assert "It is a source-code inventory, not the MCP `tools/list` contract" in first
    assert first.endswith("\n")


def test_manifest_and_public_mirror_are_current():
    generator.check_bundle()
    manifest = json.loads(generator.MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["files"].count("api-reference.md") == 1
    for file_name in manifest["files"]:
        canonical = generator.OKF_DIR / file_name
        public = generator.PUBLIC_OKF_DIR / file_name
        assert canonical.read_bytes() == public.read_bytes()
