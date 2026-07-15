"""Public intelligence packs: schema + scrub invariants."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "docs" / "public-intelligence"
MANIFEST = PACK_DIR / "packs" / "manifest.json"
SCHEMA = PACK_DIR / "public-intelligence-pack.schema.json"


def test_manifest_and_bodies_exist_and_match_schema_shape() -> None:
    assert SCHEMA.is_file()
    assert MANIFEST.is_file()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    required = set(schema["required"])
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    packs = manifest["packs"]
    assert packs, "at least one public pack required"
    for pack in packs:
        missing = required - set(pack)
        assert not missing, f"{pack.get('pack_id')}: missing {missing}"
        assert pack["scrub"]["secrets"] is True
        assert pack["scrub"]["raw_memory"] is True
        assert pack["scrub"]["private_paths"] is True
        assert pack["scrub"]["unreviewed_logs"] is True
        body = ROOT / pack["body_path"]
        assert body.is_file(), pack["body_path"]
        text = body.read_text(encoding="utf-8")
        # Hard ban on private repo name dumps and secret placeholders.
        assert "XAI_API_KEY=" not in text
        assert "unigrok-intelligence/codex" not in text
        assert "Ready for supervisor" in text or "Live" in text


def test_readme_states_promote_not_auto_sync() -> None:
    readme = (PACK_DIR / "README.md").read_text(encoding="utf-8")
    assert "distilled, reviewed recipes" in readme
    assert "Continuous auto-sync" in readme or "never" in readme.lower()
    assert "workspace-neutral" in readme
