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


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_enable_config_rejects_git_checkout(tmp_path: Path) -> None:
    """--check --enable-config must refuse a GROK_HOME that is a Git checkout."""
    grok_home = tmp_path / "fake-checkout"
    themes = grok_home / "themes"
    themes.mkdir(parents=True)
    (grok_home / ".git").mkdir()
    # Pre-seed installed themes so --check can pass before enable_config runs.
    for src_name, dest_name in (
        ("unigrok-grok-theme.toml", "unigrok.toml"),
        ("unigrok-grok-theme.json", "unigrok.json"),
        ("UniGrok.terminal", "UniGrok.terminal"),
    ):
        (themes / dest_name).write_bytes(
            (REPO / "docs" / "design" / src_name).read_bytes()
        )
    config = grok_home / "config.toml"
    config.write_text('[ui]\ntheme = "tokyonight"\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--check",
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
    assert result.returncode == 2
    assert "not a Git checkout" in result.stderr
    assert 'theme = "tokyonight"' in config.read_text(encoding="utf-8")
    assert not (grok_home / "config.toml.unigrok-theme-bak").exists()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_reject_nested_path_inside_product_checkout(tmp_path: Path) -> None:
    product = tmp_path / "product-checkout"
    nested = product / ".grok-theme-install-test"
    nested.mkdir(parents=True)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(product),
            "--grok-home",
            str(nested),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "inside the product checkout" in result.stderr
    assert not (nested / "themes").exists()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_install_rejects_installer_checkout_when_repo_differs(tmp_path: Path) -> None:
    """A mismatched --repo must not allow writes into the script checkout."""
    grok_home = REPO / ".grok-theme-install-mismatched-repo"
    other_repo = tmp_path / "other-product"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(other_repo),
            "--grok-home",
            str(grok_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "inside the product checkout" in result.stderr
    assert not grok_home.exists()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_install_rejects_symlinked_theme_directory_inside_product(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    target = REPO / ".grok-theme-symlink-target"
    (grok_home / "themes").symlink_to(target)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "theme directory must not resolve inside the product checkout" in result.stderr
    assert not target.exists()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_check_rejects_symlinked_theme_directory_inside_product(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    target = REPO / ".grok-theme-check-symlink-target"
    (grok_home / "themes").symlink_to(target)

    result = subprocess.run(
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
    )
    assert result.returncode == 2
    assert "theme directory must not resolve inside the product checkout" in result.stderr
    assert not target.exists()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_enable_config_rejects_symlinked_product_destination(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    themes = grok_home / "themes"
    themes.mkdir(parents=True)
    for src_name, dest_name in (
        ("unigrok-grok-theme.toml", "unigrok.toml"),
        ("unigrok-grok-theme.json", "unigrok.json"),
        ("UniGrok.terminal", "UniGrok.terminal"),
    ):
        (themes / dest_name).write_bytes(
            (REPO / "docs" / "design" / src_name).read_bytes()
        )
    target = REPO / ".grok-theme-config-symlink-target.toml"
    (grok_home / "config.toml").symlink_to(target)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--check",
            "--enable-config",
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "config.toml must not resolve inside the product checkout" in result.stderr
    assert not target.exists()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_install_rejects_theme_path_that_is_not_a_directory(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    themes = grok_home / "themes"
    themes.write_text("not a directory\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3
    assert "exists and is not a directory" in result.stderr


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_allow_grok_home_under_unrelated_git_root(tmp_path: Path) -> None:
    """A Git root at $HOME must not block a normal ~/.grok-style config dir."""
    home_git = tmp_path / "home"
    grok_home = home_git / ".grok"
    grok_home.mkdir(parents=True)
    (home_git / ".git").mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(REPO),
            "--grok-home",
            str(grok_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert (grok_home / "themes" / "unigrok.toml").is_file()


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_enable_config_only_rewrites_ui_theme(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    config = grok_home / "config.toml"
    config.write_text(
        '[other]\ntheme = "keep-me"\n\n[ui]\ntheme = "tokyonight"\n',
        encoding="utf-8",
    )

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
    assert 'theme = "keep-me"' in text
    assert 'theme = "unigrok"' in text
    assert 'theme = "tokyonight"' not in text


@pytest.mark.skipif(not DESIGN_TOML.is_file(), reason="theme design artifacts not on this checkout")
def test_enable_config_inserts_before_array_table(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    config = grok_home / "config.toml"
    config.write_text(
        '[ui]\nshow_timestamps = true\n\n[[profiles]]\nname = "night"\n',
        encoding="utf-8",
    )

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
    assert text.index('theme = "unigrok"') < text.index("[[profiles]]")
