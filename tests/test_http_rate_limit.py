"""HTTP /mcp and /v1 per-principal rate limiting."""

from __future__ import annotations

import pytest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from src.http_server import (
    HttpRateLimitMiddleware,
    MCPOriginMiddleware,
    create_app,
)


def test_rate_limit_middleware_is_pure_asgi():
    assert not issubclass(HttpRateLimitMiddleware, BaseHTTPMiddleware)


def test_rate_limit_fail_open_when_unset(monkeypatch):
    monkeypatch.delenv("UNIGROK_HTTP_RATE_LIMIT", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    with TestClient(create_app()) as client:
        for _ in range(5):
            res = client.get("/v1/models")
            assert res.status_code != 429


def test_rate_limit_returns_429_when_exceeded(monkeypatch):
    monkeypatch.setenv("UNIGROK_HTTP_RATE_LIMIT", "2")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/v1/models").status_code != 429
        assert client.get("/v1/models").status_code != 429
        limited = client.get("/v1/models")
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limit_exceeded"
        assert "Retry-After" in limited.headers


def test_rate_limit_does_not_apply_to_healthz(monkeypatch):
    monkeypatch.setenv("UNIGROK_HTTP_RATE_LIMIT", "1")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz").status_code == 200
