"""Notes write gate must match git mutator gate (local + ENABLE_GIT_WRITE)."""

from __future__ import annotations

import pytest

from src import workspace_memory as wm


def test_note_write_disabled_under_http_runtime(monkeypatch):
    monkeypatch.setenv("UNIGROK_SERVICE_MODE", "contributor")
    monkeypatch.setenv("UNIGROK_WORKSPACE_MEMORY", "mirror")
    monkeypatch.setenv("UNIGROK_RUNTIME", "http")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")
    assert wm._note_write_enabled() is False


def test_note_write_enabled_only_for_local(monkeypatch):
    monkeypatch.setenv("UNIGROK_SERVICE_MODE", "contributor")
    monkeypatch.setenv("UNIGROK_WORKSPACE_MEMORY", "mirror")
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")
    assert wm._note_write_enabled() is True
    monkeypatch.setenv("ENABLE_GIT_WRITE", "0")
    assert wm._note_write_enabled() is False
