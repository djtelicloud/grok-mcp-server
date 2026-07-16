"""Hard secret denylist must ignore .env even with empty gitignore."""

from __future__ import annotations

from pathlib import Path

from src.utils import is_path_ignored, validate_local_input


def test_env_file_ignored_without_gitignore(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    env = root / ".env"
    env.write_text("XAI_API_KEY=secret\n", encoding="utf-8")
    assert is_path_ignored(env, root, gitignore_patterns=[]) is True
    assert is_path_ignored(root / "src" / "main.py", root, []) is False


def test_validate_local_input_blocks_pem(tmp_path):
    pem = tmp_path / "server.pem"
    pem.write_text("x", encoding="utf-8")
    try:
        validate_local_input(pem, max_bytes=1000)
        raised = False
    except PermissionError:
        raised = True
    assert raised
