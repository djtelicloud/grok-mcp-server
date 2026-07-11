import os
import sys
from pathlib import Path

# Add the scripts directory to the path so we can import the script
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from generate_okf import extract_docs_from_file, get_keywords

def test_get_keywords():
    assert get_keywords("CamelCaseTest") == "camel, case, test"
    assert get_keywords("snake_case_test") == "snake, case, test"
    assert get_keywords("FAQDocumentError") == "faq, document, error"

def test_extract_docs_from_file(tmp_path):
    py_file = tmp_path / "test_module.py"
    py_file.write_text('''
"""Module docstring."""

class MyClass:
    """Class docstring."""
    pass

def my_func():
    """Func docstring."""
    pass

def _private_func():
    """Private func docstring."""
    pass
''')
    items = extract_docs_from_file(py_file)
    assert len(items) == 2
    
    assert items[0]["type"] == "class"
    assert items[0]["name"] == "MyClass"
    assert items[0]["docstring"] == "Class docstring."
    assert items[0]["keywords"] == "my, class"

    assert items[1]["type"] == "function"
    assert items[1]["name"] == "my_func"
    assert items[1]["docstring"] == "Func docstring."
    assert items[1]["keywords"] == "my, func"
