"""Dynamic API generation/file inflight ceilings honor live env up to platform max."""

from __future__ import annotations

import contextlib
import importlib

import pytest


def _reload_xai_api(monkeypatch: pytest.MonkeyPatch, **env: str) -> object:
    for key in (
        "UNIGROK_API_MAX_INFLIGHT",
        "UNIGROK_API_MAX_FILE_INFLIGHT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import unigrok_public.xai_api as xai_api

    return importlib.reload(xai_api)


@pytest.fixture(autouse=True)
def _restore_default_inflight_caps(monkeypatch: pytest.MonkeyPatch):
    """Keep module-level caps thrifty after each test (other suites read live values)."""
    yield
    for key in (
        "UNIGROK_API_MAX_INFLIGHT",
        "UNIGROK_API_MAX_FILE_INFLIGHT",
    ):
        monkeypatch.delenv(key, raising=False)
    import unigrok_public.xai_api as xai_api

    importlib.reload(xai_api)
    # Also refresh server module bindings if already imported.
    with contextlib.suppress(Exception):
        import unigrok_public.server as server

        importlib.reload(xai_api)
        server.xai_api = xai_api  # type: ignore[attr-defined]


def test_default_caps_thrifty(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _reload_xai_api(monkeypatch)
    assert mod.API_MAX_INFLIGHT == 4
    assert mod.API_MAX_FILE_INFLIGHT == 2


def test_env_exceeds_old_hard_16_4(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old hard caps were 16 gen / 4 file; env may now raise higher."""
    mod = _reload_xai_api(
        monkeypatch,
        UNIGROK_API_MAX_INFLIGHT="64",
        UNIGROK_API_MAX_FILE_INFLIGHT="16",
    )
    assert mod.API_MAX_INFLIGHT == 64
    assert mod.API_MAX_FILE_INFLIGHT == 16
    # Still above the historic hard ceilings
    assert mod.API_MAX_INFLIGHT > 16
    assert mod.API_MAX_FILE_INFLIGHT > 4


def test_platform_safe_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _reload_xai_api(
        monkeypatch,
        UNIGROK_API_MAX_INFLIGHT="9999",
        UNIGROK_API_MAX_FILE_INFLIGHT="9999",
    )
    assert mod.API_MAX_INFLIGHT == 256
    assert mod.API_MAX_FILE_INFLIGHT == 64


def test_floor_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _reload_xai_api(
        monkeypatch,
        UNIGROK_API_MAX_INFLIGHT="0",
        UNIGROK_API_MAX_FILE_INFLIGHT="0",
    )
    assert mod.API_MAX_INFLIGHT == 1
    assert mod.API_MAX_FILE_INFLIGHT == 1
