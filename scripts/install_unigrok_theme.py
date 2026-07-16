#!/usr/bin/env python3
"""Install the UniGrok brand theme into the Grok CLI user theme path.

Canonical source artifacts live in the product repo under ``docs/design/``.
This installer copies them to ``$GROK_HOME/themes/`` (default ``~/.grok/themes``)
so the machine is ready when Grok Build loads custom theme files.

Grok 0.2.x only resolves built-in theme *names*. Installing files is safe and
forward-compatible; selecting ``theme = "unigrok"`` may still fall back until
custom themes ship. Prefer the Apple Terminal profile for host brand match today.

Usage:
  ./scripts/install-unigrok-theme              # install files
  ./scripts/install-unigrok-theme --check      # verify install
  ./scripts/install-unigrok-theme --dry-run
  ./scripts/install-unigrok-theme --enable-config   # optional config.toml write
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL = ("name", "display_name", "truecolor_required")
REQUIRED_SLOTS = (
    "bg_base",
    "bg_light",
    "bg_dark",
    "accent_user",
    "accent_assistant",
    "accent_thinking",
    "accent_error",
    "accent_success",
    "text_primary",
    "text_secondary",
    "diff_delete_fg",
    "diff_insert_fg",
    "md_heading_h1",
    "md_code",
    "link_fg",
)
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _design_dir(repo: Path) -> Path:
    return repo / "docs" / "design"


def _grok_home(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("GROK_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".grok").resolve()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _parse_simple_toml_keys(text: str) -> dict[str, str]:
    """Parse top-level scalar keys and ``[slots]`` keys without a TOML dep.

    Theme artifacts use only simple scalars (strings/bools) for the fields we
    validate. Nested tables other than ``[slots]`` are ignored here.
    """
    out: dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if section == "slots":
            out[f"slots.{key}"] = value
        elif section == "":
            out[key] = value
    return out


def _validate_theme_toml(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    keys = _parse_simple_toml_keys(text)
    if keys.get("name") != "unigrok":
        errors.append(f"{path}: name must be \"unigrok\" (got {keys.get('name')!r})")
    for field in REQUIRED_TOP_LEVEL:
        if field not in keys:
            errors.append(f"{path}: missing top-level field {field!r}")
    for slot in REQUIRED_SLOTS:
        full = f"slots.{slot}"
        if full not in keys:
            errors.append(f"{path}: missing required slot {slot!r}")
            continue
        if not HEX_RE.match(keys[full]):
            errors.append(f"{path}: slot {slot!r} is not a #RRGGBB hex ({keys[full]!r})")
    return errors


def _sources(repo: Path) -> list[tuple[Path, str]]:
    design = _design_dir(repo)
    return [
        (design / "unigrok-grok-theme.toml", "unigrok.toml"),
        (design / "unigrok-grok-theme.json", "unigrok.json"),
        (design / "UniGrok.terminal", "UniGrok.terminal"),
    ]


def _is_inside_product(path: Path, repo: Path) -> bool:
    """Whether ``path`` resolves inside the product checkout."""

    try:
        path.expanduser().resolve().relative_to(repo.expanduser().resolve())
    except ValueError:
        return False
    return True


def _product_roots(repo: Path | None) -> list[Path]:
    """Roots that count as the product tree for write guards.

    Always include the installer's own checkout (``_repo_root()``). Also include
    an explicit ``--repo`` when present so callers cannot bypass guards by
    pointing ``--repo`` at a different tree while writing under this checkout.
    """

    roots: list[Path] = []
    for candidate in (_repo_root(), repo):
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _reject_product_write_path(path: Path, *, repo: Path | None, label: str) -> int | None:
    """Keep installer writes out of the product checkout, including via symlinks."""

    for root in _product_roots(repo):
        if not _is_inside_product(path, root):
            continue
        print(
            f"error: {label} must not resolve inside the product checkout; "
            "use a user config directory such as ~/.grok",
            file=sys.stderr,
        )
        return 2
    return None


def _reject_git_home(grok_home: Path, *, repo: Path | None = None) -> int | None:
    """Refuse GROK_HOME that is a Git root or lives inside the product checkout."""
    home = grok_home.expanduser().resolve()
    if (home / ".git").exists():
        print(
            "error: --grok-home / GROK_HOME must be a user config directory, "
            "not a Git checkout",
            file=sys.stderr,
        )
        return 2
    return _reject_product_write_path(
        home,
        repo=repo,
        label="--grok-home / GROK_HOME",
    )


def _ui_section_span(text: str) -> tuple[int, int] | None:
    """Return [start, end) character offsets of the [ui] table body, or None."""
    match = re.search(r"(?m)^\s*\[ui\]\s*(?:#.*)?$", text)
    if not match:
        return None
    start = match.end()
    next_header = re.search(
        r"(?m)^\s*\[\[?[^\]]+\]?\]\s*(?:#.*)?$", text[start:]
    )
    end = start + next_header.start() if next_header else len(text)
    return start, end


def _set_ui_theme(text: str, theme: str = "unigrok") -> tuple[str, str]:
    """
    Set [ui] theme = \"...\" only inside the [ui] table.

    Returns (new_text, status) where status is already|updated|appended.
    """
    span = _ui_section_span(text)
    theme_re = re.compile(
        rf"""(?m)^(\s*theme\s*=\s*)(["']){re.escape(theme)}\2\s*(?:#.*)?$"""
    )
    any_theme_re = re.compile(
        r"""(?m)^(\s*theme\s*=\s*)(["']).*?\2\s*(?:#.*)?$"""
    )

    if span is not None:
        start, end = span
        body = text[start:end]
        if theme_re.search(body):
            return text, "already"
        if any_theme_re.search(body):
            new_body, n = any_theme_re.subn(rf'\1"{theme}"', body, count=1)
            if n != 1 or new_body == body:
                raise ValueError("theme line in [ui] could not be updated")
            return text[:start] + new_body + text[end:], "updated"
        insert = f'\ntheme = "{theme}"'
        # Keep a trailing newline in the section when possible.
        if body.endswith("\n"):
            new_body = body.rstrip("\n") + insert + "\n"
        else:
            new_body = body + insert
        return text[:start] + new_body + text[end:], "updated"

    # No [ui] table — do not rewrite theme keys in other tables.
    appended = text.rstrip() + f'\n\n[ui]\ntheme = "{theme}"\n'
    return appended, "appended"


def install(
    *,
    repo: Path,
    grok_home: Path,
    dry_run: bool,
    force: bool,
) -> int:
    themes = grok_home / "themes"
    rejected = _reject_git_home(grok_home, repo=repo)
    if rejected is not None:
        return rejected
    rejected = _reject_product_write_path(
        themes,
        repo=repo,
        label="theme directory",
    )
    if rejected is not None:
        return rejected
    if (themes.exists() or themes.is_symlink()) and not themes.is_dir():
        print(
            f"error: {themes} exists and is not a directory; remove it before installing",
            file=sys.stderr,
        )
        return 3

    sources = _sources(repo)
    missing = [str(src) for src, _ in sources if not src.is_file()]
    if missing:
        print("error: missing design artifacts:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        print("Bring the UniGrok theme design files onto this checkout first.", file=sys.stderr)
        return 2

    errors: list[str] = []
    for src, _dest in sources:
        if src.suffix == ".toml":
            errors.extend(_validate_theme_toml(src))
    if errors:
        print("error: source theme failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    planned: list[tuple[Path, Path]] = [
        (src, themes / dest_name) for src, dest_name in sources
    ]
    # Preflight all destinations before copying so a mid-loop conflict abort
    # cannot leave a mixed partial install.
    for src, dest in planned:
        rejected = _reject_product_write_path(
            dest,
            repo=repo,
            label="theme destination",
        )
        if rejected is not None:
            return rejected
        if (dest.exists() or dest.is_symlink()) and not dest.is_file():
            print(
                f"error: {dest} exists and is not a regular file; "
                "remove it before installing",
                file=sys.stderr,
            )
            return 3
        if dest.is_file() and not force and _sha256(src) != _sha256(dest):
            print(
                f"error: {dest} exists and differs from source; re-run with --force to overwrite",
                file=sys.stderr,
            )
            return 3

    if not dry_run:
        themes.mkdir(parents=True, exist_ok=True)

    for src, dest in planned:
        if dest.is_file() and not force:
            print(f"unchanged  {dest}")
            continue
        action = "would install" if dry_run else "install"
        print(f"{action:12} {src} -> {dest}")
        if not dry_run:
            shutil.copy2(src, dest)

    print()
    print(f"Grok theme dir: {themes}")
    print("Installed names:")
    print("  unigrok.toml   # future custom theme load path")
    print("  unigrok.json   # machine-readable twin")
    print("  UniGrok.terminal  # Apple Terminal profile (import today)")
    print()
    print("Grok 0.2.x still only lists built-in theme names in /theme.")
    print("Files are ready for when custom themes load from ~/.grok/themes/.")
    print("Today: import UniGrok.terminal, or use tokyonight as a non-brand stand-in.")
    print("Optional later: set [ui] theme = \"unigrok\" (or pass --enable-config).")
    return 0


def check(*, repo: Path, grok_home: Path) -> int:
    rejected = _reject_git_home(grok_home, repo=repo)
    if rejected is not None:
        return rejected
    themes = grok_home / "themes"
    rejected = _reject_product_write_path(
        themes,
        repo=repo,
        label="theme directory",
    )
    if rejected is not None:
        return rejected
    sources = _sources(repo)
    ok = True
    for src, dest_name in sources:
        dest = themes / dest_name
        rejected = _reject_product_write_path(
            dest,
            repo=repo,
            label="theme destination",
        )
        if rejected is not None:
            return rejected
        if not src.is_file():
            print(f"FAIL source missing: {src}")
            ok = False
            continue
        if not dest.is_file():
            print(f"FAIL not installed: {dest}")
            ok = False
            continue
        if _sha256(src) != _sha256(dest):
            print(f"FAIL drift: {dest} differs from {src}")
            ok = False
            continue
        print(f"OK   {dest}")
        if dest.suffix == ".toml":
            for err in _validate_theme_toml(dest):
                print(f"FAIL {err}")
                ok = False
    if ok:
        print(f"check passed: {themes}")
        return 0
    print("check failed", file=sys.stderr)
    return 1


def enable_config(*, grok_home: Path, dry_run: bool, repo: Path | None = None) -> int:
    """Best-effort write [ui] theme = unigrok. Does not promise Grok 0.2.x loads it."""
    rejected = _reject_git_home(grok_home, repo=repo)
    if rejected is not None:
        return rejected
    config = grok_home / "config.toml"
    rejected = _reject_product_write_path(
        config,
        repo=repo,
        label="config.toml",
    )
    if rejected is not None:
        return rejected
    if not config.is_file():
        print(f"error: {config} not found; create a Grok config first", file=sys.stderr)
        return 2
    text = config.read_text(encoding="utf-8")
    try:
        new_text, status = _set_ui_theme(text, "unigrok")
    except ValueError as exc:
        print(f"error: {config}: {exc}; set theme = \"unigrok\" under [ui] manually", file=sys.stderr)
        return 2
    if status == "already":
        print(f"already set: {config} [ui] theme = \"unigrok\"")
        return 0

    backup = config.with_suffix(".toml.unigrok-theme-bak")
    print(f"{'would write' if dry_run else 'write'} {config} [ui] theme = \"unigrok\"")
    print(f"{'would backup' if dry_run else 'backup'} {backup}")
    if dry_run:
        return 0
    shutil.copy2(config, backup)
    config.write_text(new_text, encoding="utf-8")
    print(
        "note: Grok 0.2.x may ignore unknown theme names; keep Terminal profile "
        "or a stock stand-in until custom themes load."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install UniGrok brand theme into ~/.grok/themes/"
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Product checkout root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--grok-home",
        type=Path,
        default=None,
        help="Grok user home (default: $GROK_HOME or ~/.grok)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify installed files match design sources",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite differing installed files",
    )
    parser.add_argument(
        "--enable-config",
        action="store_true",
        help='Also set [ui] theme = "unigrok" in config.toml (optional; may no-op on 0.2.x)',
    )
    args = parser.parse_args(argv)

    repo = (args.repo or _repo_root()).expanduser().resolve()
    grok_home = _grok_home(str(args.grok_home) if args.grok_home else None)

    if args.check:
        code = check(repo=repo, grok_home=grok_home)
        if code != 0:
            return code
        if args.enable_config:
            return enable_config(grok_home=grok_home, dry_run=args.dry_run, repo=repo)
        return 0

    code = install(
        repo=repo,
        grok_home=grok_home,
        dry_run=args.dry_run,
        force=args.force,
    )
    if code != 0:
        return code
    if args.enable_config:
        return enable_config(grok_home=grok_home, dry_run=args.dry_run, repo=repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
