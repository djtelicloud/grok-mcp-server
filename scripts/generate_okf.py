#!/usr/bin/env python3
"""
Generate OKF-compliant API documentation from the codebase.
This script is run automatically during the landing process to keep the OKF bundle
synchronized with the main source code.
"""

import ast
import json
import re
from pathlib import Path

# Paths relative to the project root
ROOT_DIR = Path(__file__).parent.parent
SRC_DIR = ROOT_DIR / "src"
OKF_DIR = ROOT_DIR / "docs" / "okf"
API_REF_PATH = OKF_DIR / "api-reference.md"
MANIFEST_PATH = OKF_DIR / "okf-manifest.json"

def get_keywords(node_name):
    """Generate keywords from a snake_case or CamelCase node name."""
    words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+', node_name)
    words = [w.lower() for w in words]
    return ", ".join(words)

def extract_docs_from_file(file_path):
    """Extract classes, functions, and docstrings from a python file using ast."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        print(f"Skipping {file_path.name} due to parse error: {e}")
        return []

    items = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            docstring = ast.get_docstring(node)
            if docstring:
                items.append({
                    "type": "class",
                    "name": node.name,
                    "docstring": docstring,
                    "keywords": get_keywords(node.name)
                })
        elif isinstance(node, ast.FunctionDef):
            if not node.name.startswith("_"):  # Skip private functions
                docstring = ast.get_docstring(node)
                if docstring:
                    items.append({
                        "type": "function",
                        "name": node.name,
                        "docstring": docstring,
                        "keywords": get_keywords(node.name)
                    })
    return items

def generate_api_reference():
    """Generates the OKF-compliant markdown file."""
    all_items = {}
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        items = extract_docs_from_file(py_file)
        if items:
            rel_path = py_file.relative_to(SRC_DIR)
            all_items[str(rel_path)] = items

    markdown = [
        "---",
        'okf_version: "0.1"',
        'title: "API Reference"',
        'type: "api_reference"',
        'description: "Auto-generated API reference from the UniGrok codebase."',
        "---",
        "",
        "# API Reference",
        "",
        "This is the dynamic, auto-generated API reference for the UniGrok MCP server.",
        "It is synchronized natively via the landing process.",
        ""
    ]

    for module, items in all_items.items():
        module_anchor = module.replace(".py", "").replace("/", "-")
        markdown.append(f"## {module} {{#{module_anchor}}}")
        markdown.append("")
        for item in items:
            item_anchor = f"{module_anchor}-{item['name'].lower()}"
            markdown.append(f"### {item['type'].capitalize()}: `{item['name']}` {{#{item_anchor}}}")
            markdown.append(f"**Keywords:** {item['keywords']}")
            markdown.append("")
            markdown.append(item['docstring'])
            markdown.append("")

    API_REF_PATH.write_text("\n".join(markdown), encoding="utf-8")
    print(f"Generated {API_REF_PATH}")

def update_manifest():
    """Ensure the new api-reference.md is registered in the OKF manifest."""
    if not MANIFEST_PATH.exists():
        print("Manifest not found, skipping update.")
        return

    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    files = data.get("files", [])
    if "api-reference.md" not in files:
        files.append("api-reference.md")
        data["files"] = files
        MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("Updated okf-manifest.json with api-reference.md")

if __name__ == "__main__":
    print("Generating OKF API Reference...")
    generate_api_reference()
    update_manifest()
    print("Done.")
