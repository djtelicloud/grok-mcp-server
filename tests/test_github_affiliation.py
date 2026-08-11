"""Official GitHub contributor affiliation (public-safe)."""

from __future__ import annotations

import pytest

from unigrok_public import github_affiliation as ga


@pytest.fixture(autouse=True)
def _clear_affiliation_state(monkeypatch: pytest.MonkeyPatch) -> None:
    ga.clear_cache()
    ga.clear_request_github_login()
    for key in (
        "UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST",
        "UNIGROK_GITHUB_CONTRIBUTOR_REPOS",
        "UNIGROK_GITHUB_CONTRIBUTOR_ORGS",
        "UNIGROK_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "UNIGROK_GITHUB_LOGIN",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_allowlist_marks_official(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST", "CurtisFratianne, djtelicloud")
    ga.set_request_github_login("curtisfratianne")
    is_official, source = await ga.is_official_contributor()
    assert is_official is True
    assert source == "allowlist"
    view = await ga.affiliation_public_view()
    assert view["is_official_contributor"] is True
    assert view["github_login_detected"] is True
    assert view["source"] == "allowlist"
    # roster not leaked as plaintext login
    assert view.get("github_login_hint") == "cu***"


@pytest.mark.asyncio
async def test_allowlist_miss_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST", "djtelicloud")
    ga.set_request_github_login("random-user")
    is_official, source = await ga.is_official_contributor()
    assert is_official is False
    assert source == "allowlist_miss"


@pytest.mark.asyncio
async def test_no_login_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST", "djtelicloud")
    is_official, source = await ga.is_official_contributor()
    assert is_official is None
    assert source == "no_github_login"


@pytest.mark.asyncio
async def test_x_client_id_never_used_for_affiliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST", "cursor")
    # Simulate telemetry label only — no request github login set
    is_official, source = await ga.is_official_contributor()
    assert is_official is None
    assert source == "no_github_login"


@pytest.mark.asyncio
async def test_local_operator_login_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_GITHUB_LOGIN", "djtelicloud")
    monkeypatch.setenv("UNIGROK_GITHUB_CONTRIBUTOR_ALLOWLIST", "djtelicloud")
    is_official, source = await ga.is_official_contributor()
    assert is_official is True
    assert source == "allowlist"


def test_normalize_login_rejects_spoof() -> None:
    assert ga._normalize_login("../../../etc") is None
    assert ga._normalize_login("good-user") == "good-user"
    assert ga._normalize_login("@Good-User") == "good-user"
