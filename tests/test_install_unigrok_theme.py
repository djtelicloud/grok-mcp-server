"""Smoke tests for the UniGrok theme installer."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install_unigrok_theme.py"
DESIGN_TOML = REPO / "docs" / "design" / "unigrok-grok-theme.toml"


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_install_check_roundtrip(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    env = {**os.environ, "GROK_HOME": str(grok_home)}

    install = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO), "--grok-home", str(grok_home)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert install.returncode == 0, install.stderr + install.stdout
    assert (grok_home / "themes" / "unigrok.toml").is_file()
    assert (grok_home / "themes" / "unigrok.json").is_file()
    assert (grok_home / "themes" / "UniGrok.terminal").is_file()

    check = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--check",
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert check.returncode == 0, check.stderr + check.stdout
    assert "check passed" in check.stdout


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_force_overwrites_drift(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    themes = grok_home / "themes"
    themes.mkdir(parents=True)
    dest = themes / "unigrok.toml"
    dest.write_text("name = \"stale\"\n", encoding="utf-8")

    blocked = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO), "--grok-home", str(grok_home)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode == 3

    forced = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
            "--force",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert forced.returncode == 0, forced.stderr + forced.stdout
    assert 'name = "unigrok"' in dest.read_text(encoding="utf-8")


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_enable_config_writes_theme_key(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    config = grok_home / "config.toml"
    config.write_text('[ui]\ntheme = "tokyonight"\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
            "--enable-config",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    text = config.read_text(encoding="utf-8")
    assert 'theme = "unigrok"' in text
    assert (grok_home / "config.toml.unigrok-theme-bak").is_file()
