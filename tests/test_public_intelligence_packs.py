"""Public intelligence packs: schema + scrub invariants."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "docs" / "public-intelligence"
MANIFEST = PACK_DIR / "packs" / "manifest.json"
SCHEMA = PACK_DIR / "public-intelligence-pack.schema.json"


def test_manifest_and_bodies_exist_and_match_schema_shape() -> None:
    assert SCHEMA.is_file()
    assert MANIFEST.is_file()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest) == {"schema", "packs"}
    assert manifest["schema"] == "../public-intelligence-pack.schema.json"
    packs = manifest["packs"]
    assert packs, "at least one public pack required"
    banned_markers = ("XAI_API_KEY", "unigrok-intelligence/codex")
    public_metadata = json.dumps(manifest, sort_keys=True)
    for marker in banned_markers:
        assert marker not in public_metadata
    for pack in packs:
        validator.validate(pack)
        assert pack["scrub"]["secrets"] is True
        assert pack["scrub"]["raw_memory"] is True
        assert pack["scrub"]["private_paths"] is True
        assert pack["scrub"]["unreviewed_logs"] is True
        expected_body = (
            f"docs/public-intelligence/packs/{pack['version']}-{pack['pack_id']}.md"
        )
        assert pack["body_path"] == expected_body, pack["body_path"]
        body = (ROOT / pack["body_path"]).resolve()
        assert body.is_relative_to((PACK_DIR / "packs").resolve()), pack["body_path"]
        assert body.is_file(), pack["body_path"]
        text = body.read_text(encoding="utf-8")
        # Hard ban on private repo name dumps and secret placeholders.
        for marker in banned_markers:
            assert marker not in text
        assert "Ready for supervisor" in text or "Live" in text


def test_readme_states_promote_not_auto_sync() -> None:
    readme = (PACK_DIR / "README.md").read_text(encoding="utf-8")
    assert "distilled, reviewed recipes" in readme
    assert "Continuous auto-sync" in readme
    assert "workspace-neutral" in readme


def test_using_unigrok_skills_are_identical() -> None:
    public_skill = ROOT / "skills" / "using-unigrok" / "SKILL.md"
    agent_skill = ROOT / ".agents" / "skills" / "using-unigrok" / "SKILL.md"
    assert public_skill.read_text(encoding="utf-8") == agent_skill.read_text(
        encoding="utf-8"
    ), "public and agent using-unigrok skills have drifted"
