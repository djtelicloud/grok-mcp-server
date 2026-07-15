"""Deterministic public OKF wiki-mirror coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import publish_okf_wiki_mirror as mirror


def test_build_mirror_prunes_stale_pages_and_covers_manifest(tmp_path: Path) -> None:
    out = tmp_path / "wiki"
    out.mkdir()
    stale = out / "removed-source.md"
    stale.write_text("stale", encoding="utf-8")
    stale_data = out / "removed-source.json"
    stale_data.write_text("{}", encoding="utf-8")
    stale_dir = out / "removed-section"
    stale_dir.mkdir()
    (stale_dir / "page.md").write_text("stale", encoding="utf-8")

    written = mirror.build_mirror(out)

    assert not stale.exists()
    assert not stale_data.exists()
    assert not stale_dir.exists()
    assert out / "Home.md" in written
    assert out / "_Sidebar.md" in written
    manifest = json.loads(mirror.OKF_MANIFEST.read_text(encoding="utf-8"))
    for name in manifest["files"]:
        if name == manifest["root"]:
            continue
        assert (out / f"{mirror._slug(name)}.md").is_file(), name
    pack_manifest = json.loads(mirror.PACK_MANIFEST.read_text(encoding="utf-8"))
    listed_packs = [mirror.ROOT / pack["body_path"] for pack in pack_manifest["packs"]]
    for pack in listed_packs:
        assert (out / f"{mirror._slug(pack.name)}.md").is_file(), pack.name
    written_names = {path.name for path in written}
    expected_pack_names = {f"{mirror._slug(pack.name)}.md" for pack in listed_packs}
    unlisted_pack_names = {
        f"{mirror._slug(pack.name)}.md" for pack in mirror.PACKS.glob("v*.md")
    } - expected_pack_names
    assert expected_pack_names <= written_names
    assert written_names.isdisjoint(unlisted_pack_names)

    schema_page = out / "gno-envelope-v1.schema.json.md"
    schema_text = schema_page.read_text(encoding="utf-8")
    assert "**Mirror only.**" in schema_text
    assert "```json" in schema_text
    assert '"$schema"' in schema_text
    assert "Schemas and data" in (out / "_Sidebar.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["Home.md", "home.MD", "_Sidebar.md"])
def test_reserved_wiki_slugs_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="reserved wiki slug"):
        mirror._slug(name)


def test_output_directory_must_stay_outside_repository() -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        mirror.build_mirror(mirror.ROOT / "wiki-output")


def test_git_checkout_is_never_used_as_replaceable_staging(tmp_path: Path) -> None:
    out = tmp_path / "wiki-checkout"
    (out / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="staging, not a Git checkout"):
        mirror.build_mirror(out)


def test_invalid_json_error_names_the_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "broken.schema.json"
    artifact.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.schema.json"):
        mirror._json_page(artifact)


def test_subscription_auth_precedes_readiness_check_in_public_docs() -> None:
    readme = (mirror.ROOT / "README.md").read_text(encoding="utf-8")
    run_section = readme.split("## 3. Run the gateway", maxsplit=1)[1].split(
        "## 4. Connect your IDE", maxsplit=1
    )[0]
    assert run_section.index(
        "docker compose run --rm grok-cli-auth"
    ) < run_section.index("curl --fail -s http://localhost:4765/readyz")
    assert "CLI-only installs must authenticate before" in run_section
