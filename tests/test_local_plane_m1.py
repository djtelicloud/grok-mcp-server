# ruff: noqa
"""Milestone-1 local-plane acceptance tests (Tasks 1-8). Synthetic model ids only."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from unigrok_public import local_plane_loader as lpl
from unigrok_public import server
from unigrok_public.state import PublicStateStore

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> PublicStateStore:
    s = PublicStateStore(tmp_path / "state.db")
    asyncio.run(s.initialize())
    return s


@pytest.fixture
def dbpath(store: PublicStateStore, tmp_path: Path) -> Path:
    return tmp_path / "state.db"


def seed_ready(dbpath: Path | str, family: str = "fam", pattern: str = "synth") -> None:
    """Fund min-roles (router + text_generator) for substring family match."""
    conn = sqlite3.connect(str(dbpath))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO family_map (match_kind, pattern, family) VALUES (?, ?, ?)",
            ("substring", pattern, family),
        )
        router_metric = f"router:{family}:a"
        tg_metric = f"text_generator:{family}:b"
        conn.execute(
            "INSERT INTO role_floors (metric_id, role, family, filled, is_scaffold, floor_value) "
            "VALUES (?, 'router', ?, 1, 0, 0.9)",
            (router_metric, family),
        )
        conn.execute(
            "INSERT INTO role_floors (metric_id, role, family, filled, is_scaffold, floor_value) "
            "VALUES (?, 'text_generator', ?, 1, 0, 0.9)",
            (tg_metric, family),
        )
        conn.execute(
            "INSERT INTO gate_manifest (asset_key, sha256, freshness_sla_s, pinned_at) "
            "VALUES ('promote_gates', 'abc', 86400, datetime('now'))"
        )
        for role, metric_id in (("router", router_metric), ("text_generator", tg_metric)):
            conn.execute(
                "INSERT INTO promote_gates "
                "(cert_id, role, metric_id, status, family, manifest_key, certified_at) "
                "VALUES (?, ?, ?, 'certified', ?, 'promote_gates', datetime('now'))",
                (f"cert-{role}", role, metric_id, family),
            )
        conn.commit()
    finally:
        conn.close()


def _open_fk(dbpath: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(dbpath))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _reset_local_slots() -> None:
    server._LOCAL_SLOTS = None
    server._LOCAL_SLOTS_BUDGET = None


def _local_catalog(ready: bool = True, model_id: str = "synth-model-1") -> dict:
    return {
        "cli": {
            "ready": False,
            "models": [],
            "default_model": None,
        },
        "api": {
            "ready": False,
            "configured": False,
            "models": [],
            "default_model": None,
        },
        "local": {
            "ready": ready,
            "configured": True,
            "runtime_up": ready,
            "models": [model_id] if ready else [],
            "default_model": model_id if ready else None,
            "data_ready": ready,
        },
    }


# ---------------------------------------------------------------------------
# 1. DDL + default knobs
# ---------------------------------------------------------------------------


def test_fresh_db_applies_ddl_and_knobs(store: PublicStateStore) -> None:
    budget = asyncio.run(store.local_knob("local_concurrency_budget", 2))
    assert int(budget) == 2

    breaker = asyncio.run(store.local_knob("breaker_429", {}))
    assert isinstance(breaker, dict)
    assert "n" in breaker
    assert "window_s" in breaker
    assert "half_open_s" in breaker

    cont = asyncio.run(store.local_knob("continue_max_per_job_on_shed", 3))
    assert int(cont) == 3


# ---------------------------------------------------------------------------
# 2. FK: promote_gates requires gate_manifest
# ---------------------------------------------------------------------------


def test_fk_requires_manifest(dbpath: Path) -> None:
    conn = _open_fk(dbpath)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO promote_gates (cert_id, role, metric_id, status, manifest_key) "
                "VALUES ('cert-orphan', 'router', 'router:fam:orphan', 'pending', 'promote_gates')"
            )
        conn.rollback()

        conn.execute(
            "INSERT INTO gate_manifest (asset_key, sha256, freshness_sla_s, pinned_at) "
            "VALUES ('promote_gates', 'abc', 86400, datetime('now'))"
        )
        conn.execute(
            "INSERT INTO promote_gates (cert_id, role, metric_id, status, manifest_key) "
            "VALUES ('cert-orphan', 'router', 'router:fam:orphan', 'pending', 'promote_gates')"
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Empty discovery -> not ready, missing min roles
# ---------------------------------------------------------------------------


def test_empty_discovery_not_ready(dbpath: Path) -> None:
    conn = _open_fk(dbpath)
    try:
        stats = lpl.rewrite_at_load(conn, [])
        assert stats.ready_candidate is False
        missing = set(stats.missing_min_roles)
        assert missing == {"router", "text_generator"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Unknown family -> error, no binds, not ready
# ---------------------------------------------------------------------------


def test_unknown_family_error_no_bind(dbpath: Path) -> None:
    conn = _open_fk(dbpath)
    try:
        stats = lpl.rewrite_at_load(
            conn,
            [lpl.DiscoveredModel("synth-x", "synth-x")],
        )
        errors = list(stats.errors)
        assert any("no_family:synth-x" in str(e) for e in errors)
        assert lpl.get_bind(conn, "synth-x", "router") is None
        assert lpl.get_bind(conn, "synth-x", "text_generator") is None
        assert stats.ready_candidate is False
        assert lpl.plane_data_ready(conn) is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Full ready path + get_bind + role-fit split (judge unbound)
# ---------------------------------------------------------------------------


def test_full_ready_path_and_get_bind(dbpath: Path) -> None:
    seed_ready(dbpath)
    conn = _open_fk(dbpath)
    try:
        stats = lpl.rewrite_at_load(
            conn,
            [lpl.DiscoveredModel("synth-model-1", "synth-model-1")],
        )
        assert stats.ready_candidate is True
        bind = lpl.get_bind(conn, "synth-model-1", "router")
        assert bind is not None
        assert bind.metric_id == "router:fam:a"
        assert lpl.get_bind(conn, "synth-model-1", "judge") is None
        assert lpl.plane_data_ready(conn) is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Second rewrite rebuilds binds
# ---------------------------------------------------------------------------


def test_second_rewrite_rebuilds(dbpath: Path) -> None:
    seed_ready(dbpath)
    conn = _open_fk(dbpath)
    try:
        lpl.rewrite_at_load(
            conn,
            [lpl.DiscoveredModel("synth-model-1", "synth-model-1")],
        )
        assert lpl.get_bind(conn, "synth-model-1", "router") is not None

        lpl.rewrite_at_load(
            conn,
            [lpl.DiscoveredModel("synth-model-2", "synth-model-2")],
        )
        assert lpl.get_bind(conn, "synth-model-1", "router") is None
        assert lpl.get_bind(conn, "synth-model-2", "router") is not None
        assert lpl.get_bind(conn, "synth-model-2", "text_generator") is not None
        n = conn.execute("SELECT COUNT(*) FROM runtime_binds").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Scaffold-only floors never ready
# ---------------------------------------------------------------------------


def test_scaffold_only_never_ready(dbpath: Path) -> None:
    seed_ready(dbpath)
    conn = _open_fk(dbpath)
    try:
        conn.execute(
            "UPDATE role_floors SET filled = 0, is_scaffold = 1, floor_value = NULL"
        )
        conn.commit()
        stats = lpl.rewrite_at_load(
            conn,
            [lpl.DiscoveredModel("synth-model-1", "synth-model-1")],
        )
        assert stats.ready_candidate is False
        assert lpl.plane_data_ready(conn) is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 8. Stale gate manifest -> not ready
# ---------------------------------------------------------------------------


def test_stale_manifest_not_ready(dbpath: Path) -> None:
    seed_ready(dbpath)
    conn = _open_fk(dbpath)
    try:
        conn.execute(
            "UPDATE gate_manifest SET pinned_at = datetime('now', '-2 days') "
            "WHERE asset_key = 'promote_gates'"
        )
        conn.commit()
        stats = lpl.rewrite_at_load(
            conn,
            [lpl.DiscoveredModel("synth-model-1", "synth-model-1")],
        )
        assert stats.ready_candidate is False
        assert lpl.plane_data_ready(conn) is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 9. Scorecard import is scaffold-only (never marks ready)
# ---------------------------------------------------------------------------


def test_scorecard_import_scaffold_only(store: PublicStateStore, tmp_path: Path) -> None:
    seed_dir = tmp_path / "seed_assets"
    seed_dir.mkdir()

    (seed_dir / "family_map.json").write_text(
        json.dumps(
            [
                {
                    "match_kind": "substring",
                    "pattern": "synth",
                    "family": "fam",
                }
            ]
        ),
        encoding="utf-8",
    )
    (seed_dir / "dialect_matrix.json").write_text(
        json.dumps({"fam": {"system_lock": "synthetic lock"}}),
        encoding="utf-8",
    )
    (seed_dir / "gate_manifest.json").write_text(
        json.dumps(
            [
                {
                    "asset_key": "promote_gates",
                    "sha256": "abc",
                    "freshness_sla_s": 86400,
                    "pinned_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    (seed_dir / "promote.json").write_text(json.dumps([]), encoding="utf-8")
    (seed_dir / "traps.json").write_text(json.dumps([]), encoding="utf-8")
    (seed_dir / "scorecard.json").write_text(
        json.dumps(
            [
                {
                    "checkpoint": "ckpt-synth-1",
                    "family": "fam",
                    "role": "router",
                    "floor": 0.91,
                    "n": 150,
                },
                {
                    "checkpoint": "ckpt-synth-1",
                    "family": "fam",
                    "role": "text_generator",
                    "floor": 0.88,
                    "n": 150,
                },
            ]
        ),
        encoding="utf-8",
    )

    asyncio.run(store.local_seed_assets(str(seed_dir)))

    db = tmp_path / "state.db"
    conn = _open_fk(db)
    try:
        rows = conn.execute(
            "SELECT filled, is_scaffold, model_id, scorecard_src FROM role_floors"
        ).fetchall()
        assert rows, "expected scaffold role_floors rows from scorecard import"
        for filled, is_scaffold, model_id, scorecard_src in rows:
            assert int(filled) == 0
            assert int(is_scaffold) == 1
            assert model_id is None
            assert scorecard_src is not None and str(scorecard_src) != ""
    finally:
        conn.close()

    assert asyncio.run(store.local_data_ready()) is False


# ---------------------------------------------------------------------------
# 10. Facade ready conjunction + role-fit split (Task 8)
# ---------------------------------------------------------------------------


def test_facade_ready_conjunction(store: PublicStateStore, dbpath: Path) -> None:
    asyncio.run(store.rewrite_local_binds([]))
    assert asyncio.run(store.local_data_ready()) is False

    seed_ready(dbpath)
    asyncio.run(store.rewrite_local_binds([{"model_id": "synth-model-1"}]))
    assert asyncio.run(store.local_data_ready()) is True

    # judge has no floor; plane ready stays True (min-roles only)
    assert asyncio.run(store.local_bind("synth-model-1", "judge")) is None
    assert asyncio.run(store.local_data_ready()) is True


# ---------------------------------------------------------------------------
# 11. Alternate-plane prefers local when slot free; respects budget / readiness
# ---------------------------------------------------------------------------


def test_alternate_plane_prefers_local_with_slot(
    store: PublicStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_local_slots()
    monkeypatch.setattr(server, "STATE", store)
    catalogs = _local_catalog(ready=True, model_id="synth-model-1")
    monkeypatch.setattr(server, "_catalogs", AsyncMock(return_value=catalogs))

    alt = asyncio.run(server._alternate_plane("cli", None, requires_api=False))
    assert alt == "local"
    server._local_slot_release()

    # Exhaust concurrency budget (default 2): no free local slot -> None
    async def _exhaust() -> None:
        assert await server._local_slot_acquire() is True
        assert await server._local_slot_acquire() is True
        alt_exhausted = await server._alternate_plane("cli", None, requires_api=False)
        assert alt_exhausted is None

    asyncio.run(_exhaust())
    _reset_local_slots()

    # Local not ready -> None
    catalogs_down = _local_catalog(ready=False)
    monkeypatch.setattr(server, "_catalogs", AsyncMock(return_value=catalogs_down))
    alt_down = asyncio.run(server._alternate_plane("cli", None, requires_api=False))
    assert alt_down is None
    _reset_local_slots()


# ---------------------------------------------------------------------------
# 12. Offline serve receipts (happy + router-unfunded degraded)
# ---------------------------------------------------------------------------


class _LocalRuntimeHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-shaped local runtime for offline serve tests."""

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] in ("/v1/models", "/v1/models/"):
            body = json.dumps({"data": [{"id": "synth-model-1"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}

        if path not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self.send_response(404)
            self.end_headers()
            return

        messages = payload.get("messages") or []
        system = ""
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                system = str(msg.get("content") or "")
                break

        if "Reply ONLY with JSON" in system:
            content = json.dumps({"route": "direct", "brief": "synthetic brief"})
        else:
            content = "WAL is a write-ahead log used for durability."

        body = json.dumps(
            {
                "id": "chatcmpl-synth",
                "object": "chat.completion",
                "model": str(payload.get("model") or "synth-model-1"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_local_runtime() -> tuple[HTTPServer, str, threading.Thread]:
    httpd = HTTPServer(("127.0.0.1", 0), _LocalRuntimeHandler)
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, base, thread


def test_offline_serve_receipts(
    store: PublicStateStore,
    dbpath: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_ready(dbpath)
    asyncio.run(store.rewrite_local_binds([{"model_id": "synth-model-1"}]))
    assert asyncio.run(store.local_data_ready()) is True

    httpd, base, _thread = _start_local_runtime()
    try:
        _reset_local_slots()
        monkeypatch.setattr(server, "STATE", store)
        monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", base)
        monkeypatch.setattr(server, "_CATALOG_CACHE", None)
        catalogs = _local_catalog(ready=True, model_id="synth-model-1")
        monkeypatch.setattr(server, "_catalogs", AsyncMock(return_value=catalogs))

        result = asyncio.run(server._serve_local_offline("What is a WAL?"))
        assert result["billing_class"] == "local_runtime"
        assert float(result["cost_usd"]) == 0.0
        # m3-task1: contract 6.2 requires degraded=true on successful local serves
        assert result.get("degraded") is True
        orch = result.get("orchestration") or {}
        assert orch.get("brief_source") == "local_router_floor"
        assert orch.get("router_source") == "heuristic"

        # Degraded: remove router runtime bind -> router floor unfunded
        conn = _open_fk(dbpath)
        try:
            conn.execute("DELETE FROM runtime_binds WHERE role = 'router'")
            conn.commit()
        finally:
            conn.close()

        _reset_local_slots()
        monkeypatch.setattr(server, "_CATALOG_CACHE", None)
        degraded = asyncio.run(server._serve_local_offline("What is a WAL?"))
        assert degraded.get("degraded") is True
        assert degraded.get("fallback_reason") == "local_router_floor_unfunded"
        assert degraded.get("billing_class") == "local_runtime"
        assert float(degraded.get("cost_usd") or 0.0) == 0.0
    finally:
        httpd.shutdown()
        _reset_local_slots()


# ---------------------------------------------------------------------------
# M2-T1 — runtime-agnostic probe interface + thin _probe_local adapter
# ---------------------------------------------------------------------------


class _FakeProbe:
    """Injectable ProbeBackend for tests (no network, no httpx)."""

    name = "fake"

    def __init__(self, result: lpl.ProbeResult) -> None:
        self._result = result
        self.calls: list[tuple[str, float]] = []

    async def list_models(self, base_url: str, timeout: float) -> lpl.ProbeResult:
        self.calls.append((base_url, float(timeout)))
        return self._result


def test_probe_local_fake_runtime_up_zero_models(store, monkeypatch):
    """FakeProbe runtime_up=True, 0 models -> rewrite([]) and ready=False."""
    rewrite_calls: list[list] = []

    async def _capture_rewrite(discovered):
        rewrite_calls.append(list(discovered))
        return {
            "ok": True,
            "ready_candidate": False,
            "missing_min_roles": ["router", "text_generator"],
            "errors": [],
            "binds": [],
        }

    fake = _FakeProbe(lpl.ProbeResult(runtime_up=True, models=()))
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    monkeypatch.setattr(store, "rewrite_local_binds", _capture_rewrite)

    out = asyncio.run(server._probe_local())
    assert out["configured"] is True
    assert out["runtime_up"] is True
    assert out["models"] == []
    assert out["default_model"] is None
    assert out["ready"] is False
    assert out["data_ready"] is False
    assert out["runtime_kind"] is None
    assert rewrite_calls == [[]]
    assert len(fake.calls) == 1


def test_probe_local_fake_two_models_adapters_no_http(store, monkeypatch):
    """FakeProbe with 2 models (adapters) -> rewrite gets full dicts; no HTTP."""
    rewrite_calls: list[list] = []

    async def _capture_rewrite(discovered):
        rewrite_calls.append(list(discovered))
        return {
            "ok": True,
            "ready_candidate": True,
            "missing_min_roles": [],
            "errors": [],
            "binds": [],
        }

    models = (
        lpl.DiscoveredModel(
            model_id="synth-a",
            raw_name="synth-a",
            runtime="openai_compat",
            adapters=("chat",),
        ),
        lpl.DiscoveredModel(
            model_id="synth-b",
            raw_name="raw-b",
            runtime="openai_compat",
            adapters=(),
        ),
    )
    fake = _FakeProbe(lpl.ProbeResult(runtime_up=True, models=models))
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    monkeypatch.setattr(store, "rewrite_local_binds", _capture_rewrite)

    out = asyncio.run(server._probe_local())
    assert out["runtime_up"] is True
    assert out["models"] == ["synth-a", "synth-b"]
    assert out["default_model"] == "synth-a"
    assert out["runtime_kind"] == "openai_compat"
    assert len(rewrite_calls) == 1
    assert rewrite_calls[0] == [
        {
            "model_id": "synth-a",
            "raw_name": "synth-a",
            "runtime": "openai_compat",
            "adapters": ["chat"],
        },
        {
            "model_id": "synth-b",
            "raw_name": "raw-b",
            "runtime": "openai_compat",
            "adapters": [],
        },
    ]
    assert len(fake.calls) == 1


def test_probe_local_unconfigured_skips_backends(monkeypatch):
    """LOCAL_RUNTIME_URL unset -> configured=False; probe backends never invoked."""
    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="should-not-see",
                    raw_name="should-not-see",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)

    out = asyncio.run(server._probe_local())
    assert out == {
        "configured": False,
        "ready": False,
        "runtime_up": False,
        "models": [],
        "default_model": None,
        "data_ready": False,
    }
    assert fake.calls == []


def test_probe_runtime_chain_second_backend_wins():
    """First backend down, second up -> result uses second backend's models."""
    down = _FakeProbe(
        lpl.ProbeResult(runtime_up=False, errors=("first:down",))
    )
    up = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="from-second",
                    raw_name="from-second",
                    runtime="ollama",
                ),
            ),
        )
    )
    result = asyncio.run(
        lpl.probe_runtime(
            "http://local-runtime.test",
            timeout=1.0,
            backends=(down, up),
        )
    )
    assert result.runtime_up is True
    assert len(result.models) == 1
    assert result.models[0].model_id == "from-second"
    assert result.models[0].runtime == "ollama"
    assert len(down.calls) == 1
    assert len(up.calls) == 1


def test_probe_runtime_mlx_injected_chain_runtime_tag():
    """MLXProbe via injected chain tags models runtime='mlx' (pluggable shape)."""

    class _StaticMLX:
        name = "mlx"

        async def list_models(self, base_url: str, timeout: float) -> lpl.ProbeResult:
            return lpl.ProbeResult(
                runtime_up=True,
                models=(
                    lpl.DiscoveredModel(
                        model_id="mlx-synth-1",
                        raw_name="mlx-synth-1",
                        runtime="mlx",
                    ),
                ),
            )

    # Real MLXProbe class exists and is not in the default chain.
    assert any(b.name == "openai_compat" for b in lpl.DEFAULT_PROBE_BACKENDS)
    assert any(b.name == "ollama" for b in lpl.DEFAULT_PROBE_BACKENDS)
    assert all(b.name != "mlx" for b in lpl.DEFAULT_PROBE_BACKENDS)
    assert lpl.MLXProbe.name == "mlx"

    result = asyncio.run(
        lpl.probe_runtime(
            "http://local-runtime.test",
            timeout=1.0,
            backends=(_StaticMLX(),),
        )
    )
    assert result.runtime_up is True
    assert result.models[0].runtime == "mlx"
    assert result.models[0].model_id == "mlx-synth-1"


def test_probe_runtime_all_down_accumulates_errors():
    a = _FakeProbe(lpl.ProbeResult(runtime_up=False, errors=("a:fail",)))
    b = _FakeProbe(lpl.ProbeResult(runtime_up=False, errors=("b:fail",)))
    result = asyncio.run(
        lpl.probe_runtime(
            "http://local-runtime.test",
            timeout=1.0,
            backends=(a, b),
        )
    )
    assert result.runtime_up is False
    assert result.models == ()
    assert result.errors == ("a:fail", "b:fail")


# ---------------------------------------------------------------------------
# M2-T2 — _local_op_discover + _local_op_health (thin, receipts-honest)
# ---------------------------------------------------------------------------


def _patch_catalog_peers(monkeypatch) -> None:
    """Stub cli/api probes so _catalogs can run without network."""
    not_ready = {"ready": False, "models": [], "default_model": None}
    monkeypatch.setattr(server, "_probe_cli", AsyncMock(return_value=dict(not_ready)))
    monkeypatch.setattr(
        server.xai_api, "probe_models", AsyncMock(return_value=dict(not_ready))
    )


def test_local_op_health_runtime_up_no_certified_model(store, monkeypatch):
    """FakeProbe up + ready_candidate False -> healthy=False, reason=runtime_up_no_certified_model."""
    async def _rewrite_no_floor(discovered):
        return {
            "ok": True,
            "ready_candidate": False,
            "missing_min_roles": ["router", "text_generator"],
            "errors": [],
            "binds": [],
        }

    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="unbound-model",
                    raw_name="unbound-model",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    monkeypatch.setattr(store, "rewrite_local_binds", _rewrite_no_floor)
    _patch_catalog_peers(monkeypatch)

    health = asyncio.run(server._local_op_health(refresh=True))
    assert health["healthy"] is False
    assert health["reason"] == "runtime_up_no_certified_model"
    assert health["runtime_up"] is True
    assert health["data_ready"] is False
    assert "router" in health["missing_min_roles"]
    assert "text_generator" in health["missing_min_roles"]


def test_local_op_health_runtime_down_even_with_seed(store, dbpath, monkeypatch):
    """FakeProbe runtime_up=False -> runtime_down even when seed_ready data exists."""
    seed_ready(dbpath, family="fam", pattern="synth")
    fake = _FakeProbe(lpl.ProbeResult(runtime_up=False, errors=("down",)))
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    health = asyncio.run(server._local_op_health(refresh=True))
    assert health["healthy"] is False
    assert health["reason"] == "runtime_down"
    assert health["runtime_up"] is False


def test_local_op_health_healthy_with_seed_and_matching_model(
    store, dbpath, monkeypatch
):
    """FakeProbe up + seed_ready + synth model id -> healthy=True via real rewrite binds."""
    seed_ready(dbpath, family="fam", pattern="synth")
    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="synth-model-1",
                    raw_name="synth-model-1",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    health = asyncio.run(server._local_op_health(refresh=True))
    assert health["healthy"] is True
    assert health["reason"] == "healthy"
    assert health["runtime_up"] is True
    assert health["data_ready"] is True
    assert health["missing_min_roles"] == []


def test_local_op_discover_adapters_from_probe(store, monkeypatch):
    """Discover carries probe adapters; empty adapters stay []."""
    async def _rewrite_ok(discovered):
        return {
            "ok": True,
            "ready_candidate": False,
            "missing_min_roles": ["router", "text_generator"],
            "errors": [],
            "binds": [],
        }

    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="synth-a",
                    raw_name="synth-a",
                    runtime="openai_compat",
                    adapters=("lora-x",),
                ),
                lpl.DiscoveredModel(
                    model_id="synth-b",
                    raw_name="raw-b",
                    runtime="openai_compat",
                    adapters=(),
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    monkeypatch.setattr(store, "rewrite_local_binds", _rewrite_ok)
    _patch_catalog_peers(monkeypatch)

    discover = asyncio.run(server._local_op_discover(refresh=True))
    assert discover["configured"] is True
    assert discover["runtime_up"] is True
    assert discover["runtime_kind"] == "openai_compat"
    assert discover["default_model"] == "synth-a"
    assert len(discover["models"]) == 2
    assert discover["models"][0]["adapters"] == ["lora-x"]
    assert discover["models"][1]["adapters"] == []
    assert discover["models"][0]["model_id"] == "synth-a"
    assert discover["models"][1]["raw_name"] == "raw-b"
    assert "missing_min_roles" in discover["rewrite"]
    assert "errors" in discover["rewrite"]


def test_local_op_health_not_configured(monkeypatch):
    """LOCAL_RUNTIME_URL unset -> reason=not_configured."""
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", ())
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    health = asyncio.run(server._local_op_health(refresh=True))
    assert health["healthy"] is False
    assert health["reason"] == "not_configured"
    assert health["runtime_up"] is False


# ---------------------------------------------------------------------------
# M2-T3 — _local_op_role_fit (first-class) + _local_op_capabilities
# (acceptance tests by task-master; op code authored by Grok)
# ---------------------------------------------------------------------------


def _ready_local_env(store, dbpath, monkeypatch, adapters=()):
    """Seed min roles + FakeProbe with one synth model; real STATE binds it."""
    seed_ready(dbpath, family="fam", pattern="synth")
    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="synth-model-1",
                    raw_name="synth-model-1",
                    runtime="openai_compat",
                    adapters=tuple(adapters),
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)


def test_role_fit_judge_no_floor_while_plane_healthy(store, dbpath, monkeypatch):
    """Missing judge floor -> no_floor on that request; plane health unaffected."""
    _ready_local_env(store, dbpath, monkeypatch)
    health = asyncio.run(server._local_op_health(refresh=True))
    assert health["healthy"] is True
    fit = asyncio.run(server._local_op_role_fit("judge"))
    assert fit["fit"] is False
    assert fit["reason"] == "no_floor"


def test_role_fit_explicit_model_matches_state_bind(store, dbpath, monkeypatch):
    """Explicit model_id fit values match STATE.local_bind; judge stays no_floor."""
    _ready_local_env(store, dbpath, monkeypatch)
    asyncio.run(server._catalogs(refresh=True))
    fit = asyncio.run(
        server._local_op_role_fit("router", model_id="synth-model-1")
    )
    assert fit["fit"] is True and fit["reason"] == "ok"
    bind = asyncio.run(store.local_bind("synth-model-1", "router"))
    assert bind is not None
    assert fit["metric_id"] == bind["metric_id"]
    assert fit["cert_id"] == bind["cert_id"]
    judge = asyncio.run(
        server._local_op_role_fit("judge", model_id="synth-model-1")
    )
    assert judge["fit"] is False and judge["reason"] == "no_floor"
    # alias delegates to the op
    alias = asyncio.run(server._local_role_fit("router", model_id="synth-model-1"))
    assert alias == fit


def test_capabilities_roles_pins_adapters_budget(store, dbpath, monkeypatch):
    """Capabilities derive from binds + discover + knobs; nothing invented."""
    _ready_local_env(store, dbpath, monkeypatch, adapters=("lora-x",))
    asyncio.run(server._catalogs(refresh=True))
    caps = asyncio.run(server._local_op_capabilities())
    assert caps["plane"] == "local"
    assert caps["ready"] is True
    assert caps["roles"] == ["router", "text_generator"]
    assert caps["models"] == ["synth-model-1"]
    pin_roles = {b["role"] for b in caps["binds"]}
    assert pin_roles == {"router", "text_generator"}
    for b in caps["binds"]:
        assert b["metric_id"]
    assert caps["adapters"] == ["lora-x"]
    assert caps["concurrency_budget"] == 2
    assert caps["runtime_kind"] == "openai_compat"


def test_capabilities_exclude_unbound_discover_includes(store, dbpath, monkeypatch):
    """Discovered-but-unbound model shows in discover, not in capability models."""
    seed_ready(dbpath, family="fam", pattern="synth")
    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="synth-model-1",
                    raw_name="synth-model-1",
                    runtime="openai_compat",
                ),
                lpl.DiscoveredModel(
                    model_id="unmatched-1",
                    raw_name="unmatched-1",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    discover = asyncio.run(server._local_op_discover(refresh=True))
    ids = [m["model_id"] for m in discover["models"]]
    assert "unmatched-1" in ids
    caps = asyncio.run(server._local_op_capabilities())
    assert "unmatched-1" not in caps["models"]
    assert caps["models"] == ["synth-model-1"]


# ---------------------------------------------------------------------------
# M2-T4 — _local_op_invoke thin adapter over _local_chat
# ---------------------------------------------------------------------------


def test_local_op_invoke_no_floor(store, dbpath, monkeypatch):
    """(a) role-fit fail-closed: no chat, no slot release."""
    server._CATALOG_CACHE = None
    chat = AsyncMock()
    release_calls: list[None] = []
    monkeypatch.setattr(server, "_local_chat", chat)
    monkeypatch.setattr(server, "_local_slot_release", lambda: release_calls.append(None))

    out = asyncio.run(server._local_op_invoke("hi", role="router"))
    assert out["ok"] is False
    assert out["reason"] in {"plane_not_ready", "no_floor"}
    assert out.get("content") is None
    assert out["floor_role"] == "router"
    assert out["floor_metric_ids"] == []
    chat.assert_not_awaited()
    assert release_calls == []


def test_local_op_invoke_funded_router(store, dbpath, monkeypatch):
    """(b) funded router: maps _local_chat envelope + floor receipts."""
    _ready_local_env(store, dbpath, monkeypatch)
    server._CATALOG_CACHE = None
    asyncio.run(server._catalogs(refresh=True))

    chat = AsyncMock(
        return_value={
            "text": "x",
            "model": "synth-model-1",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }
    )
    monkeypatch.setattr(server, "_local_chat", chat)

    out = asyncio.run(server._local_op_invoke("hi", role="router"))
    assert out["ok"] is True
    assert out["reason"] == "ok"
    assert out["billing_class"] == "local_runtime"
    assert out["cost_usd"] == 0.0
    assert out["floor_role"] == "router"

    bind = asyncio.run(server.STATE.local_bind("synth-model-1", "router"))
    assert bind is not None
    assert out["floor_metric_ids"] == [bind["metric_id"]]

    chat.assert_awaited_once()
    _args, kwargs = chat.await_args
    assert kwargs.get("role") == "router"
    assert kwargs.get("model_id") == "synth-model-1"


def test_local_op_invoke_local_busy(store, dbpath, monkeypatch):
    """(c) capacity: local_busy before try — no chat, no release."""
    _ready_local_env(store, dbpath, monkeypatch)
    server._CATALOG_CACHE = None
    asyncio.run(server._catalogs(refresh=True))

    chat = AsyncMock()
    release_calls: list[None] = []
    monkeypatch.setattr(server, "_local_chat", chat)
    monkeypatch.setattr(server, "_local_slot_acquire", AsyncMock(return_value=False))
    monkeypatch.setattr(server, "_local_slot_release", lambda: release_calls.append(None))

    out = asyncio.run(server._local_op_invoke("hi", role="router"))
    assert out["ok"] is False
    assert out["reason"] == "local_busy"
    chat.assert_not_awaited()
    assert release_calls == []


def test_local_op_invoke_transport_error(store, dbpath, monkeypatch):
    """(d) transport error: invoke_error*, slot released once."""
    _ready_local_env(store, dbpath, monkeypatch)
    server._CATALOG_CACHE = None
    asyncio.run(server._catalogs(refresh=True))

    chat = AsyncMock(side_effect=RuntimeError("boom"))
    release_calls: list[None] = []
    monkeypatch.setattr(server, "_local_chat", chat)
    monkeypatch.setattr(server, "_local_slot_release", lambda: release_calls.append(None))

    out = asyncio.run(server._local_op_invoke("hi", role="router"))
    assert out["ok"] is False
    assert str(out["reason"]).startswith("invoke_error")
    assert "boom" in str(out.get("error") or "")
    assert len(release_calls) == 1


# ---------------------------------------------------------------------------
# M2-T5 — router_models catalog data + _lead_model("local") fail-closed
# (tests reconciled to committed style by task-master; ops authored by Grok)
# ---------------------------------------------------------------------------


def test_lead_model_local_zero_router_binds(store, dbpath, monkeypatch):
    """(a)+(c) no router binds -> router_models=[], lead None, ready False while up."""
    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="unmatched-1",
                    raw_name="unmatched-1",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    catalogs = asyncio.run(server._catalogs(refresh=True))
    local = catalogs["local"]
    assert local["runtime_up"] is True
    assert local["router_models"] == []
    assert server._lead_model(catalogs, "local") is None
    assert local["ready"] is False


def test_lead_model_local_router_bound_not_first_string(store, dbpath, monkeypatch):
    """(b) default_model is first discovered; local lead is the router-bound model."""
    seed_ready(dbpath, family="fam", pattern="synth")
    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="unmatched-1",
                    raw_name="unmatched-1",
                    runtime="openai_compat",
                ),
                lpl.DiscoveredModel(
                    model_id="synth-model-1",
                    raw_name="synth-model-1",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)

    catalogs = asyncio.run(server._catalogs(refresh=True))
    local = catalogs["local"]
    assert local["default_model"] == "unmatched-1"
    assert "synth-model-1" in local["router_models"]
    assert "unmatched-1" not in local["router_models"]
    assert server._lead_model(catalogs, "local") == "synth-model-1"


def test_lead_model_cli_api_regression_unchanged():
    """(d) committed cli/api semantics unchanged."""
    catalogs = {
        "cli": {
            "default_model": "m-lead",
            "models": ["m-lead"],
            "ready": True,
        },
        "api": {"models": [], "default_model": None},
        "local": {},
    }
    assert server._lead_model(catalogs, "cli") == "m-lead"
    assert server._lead_model(catalogs, "local") is None


# ---------------------------------------------------------------------------
# M2-T6 — receipt honesty: canonical trigger + floor pins on local paths
# ---------------------------------------------------------------------------


def test_canonical_trigger_mapping():
    assert server._canonical_trigger(None) == "none"
    assert server._canonical_trigger("") == "none"
    assert server._canonical_trigger("local_router_floor_unfunded") == "no_floor"
    assert server._canonical_trigger("local router floor unfunded (no_floor)") == "no_floor"
    assert server._canonical_trigger("cli_circuit_open") == "breaker_open"
    assert server._canonical_trigger("cli_timeout") == "timeout"
    assert server._canonical_trigger("api_rate_limited") == "429"
    assert server._canonical_trigger("cli_incomplete_response") == "non_answer"
    assert server._canonical_trigger("local_concurrency_exhausted") == "shed"
    assert server._canonical_trigger("cli_capability_unavailable") == "missing"
    assert server._canonical_trigger("cli_runtime_failure") == "error"


def test_receipt_trigger_happy_and_fallback():
    happy = server._receipt(
        {"text": "ok"},
        requested_plane="auto",
        resolved_plane="cli",
        fallback_policy="auto",
    )
    assert happy["trigger"] == "none"
    assert happy["degraded"] is False
    assert happy["fallback_occurred"] is False

    fb = server._receipt(
        {"text": "ok"},
        requested_plane="auto",
        resolved_plane="local",
        fallback_policy="auto",
        fallback_from="cli",
        fallback_reason="cli_timeout",
    )
    assert fb["trigger"] == "timeout"
    assert fb["degraded"] is True
    assert fb["fallback_occurred"] is True
    assert fb["fallback_from"] == "cli"
    assert fb["fallback_reason"] == "cli_timeout"
    assert fb["resolved_plane"] == "local"


def test_offline_degraded_trigger_shed_and_no_floor(store, dbpath, monkeypatch):
    _ready_local_env(store, dbpath, monkeypatch)
    _reset_local_slots()

    monkeypatch.setattr(
        server, "_local_slot_acquire", AsyncMock(return_value=False)
    )
    out_shed = asyncio.run(server._serve_local_offline("hi"))
    assert out_shed["degraded"] is True
    assert out_shed["fallback_reason"] == "local_concurrency_exhausted"
    assert out_shed["trigger"] == "shed"

    async def _slot_ok() -> bool:
        return True

    monkeypatch.setattr(server, "_local_slot_acquire", _slot_ok)
    monkeypatch.setattr(
        server,
        "_local_router_floor",
        AsyncMock(
            side_effect=RuntimeError("local router floor unfunded (no_floor)")
        ),
    )
    out_nf = asyncio.run(server._serve_local_offline("hi"))
    assert out_nf["degraded"] is True
    assert out_nf["fallback_reason"] == "local_router_floor_unfunded"
    assert out_nf["trigger"] == "no_floor"


def test_offline_success_trigger_and_floor_pins(store, dbpath, monkeypatch):
    _ready_local_env(store, dbpath, monkeypatch)
    _reset_local_slots()
    asyncio.run(server._catalogs(refresh=True))

    monkeypatch.setattr(
        server,
        "_local_router_floor",
        AsyncMock(
            return_value={
                "route": "direct",
                "brief": "b",
                "router_model": "synth-model-1",
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "_local_chat",
        AsyncMock(
            return_value={
                "text": "x",
                "model": "synth-model-1",
                "plane": "local",
                "billing_class": "local_runtime",
                "cost_usd": 0.0,
                "stop_reason": "stop",
            }
        ),
    )

    out = asyncio.run(server._serve_local_offline("hi"))
    bind = asyncio.run(store.local_bind("synth-model-1", "text_generator"))
    assert bind is not None
    assert out["trigger"] == "none"
    assert out["model_id"] == "synth-model-1"
    assert out["floor_role"] == "text_generator"
    assert out["floor_metric_ids"] == [bind["metric_id"]]
    # m3-task1: contract 6.2 requires degraded=true on successful local serves
    assert out["degraded"] is True
    assert out["fallback_occurred"] is False
    assert out["orchestration"]["brief_source"] == "local_router_floor"
    assert out["orchestration"]["router_source"] in (
        "local_router_floor",
        "heuristic",
    )


# ---------------------------------------------------------------------------
# M2-T7 — skill coverage honesty on offline path (§3.4)
# ---------------------------------------------------------------------------


def test_serve_local_offline_media_fail_closed(store, dbpath, monkeypatch):
    """Media prompt -> no_floor; router/specialist never invoked."""
    _ready_local_env(store, dbpath, monkeypatch)
    _reset_local_slots()
    router_mock = AsyncMock()
    chat_mock = AsyncMock()
    monkeypatch.setattr(server, "_local_router_floor", router_mock)
    monkeypatch.setattr(server, "_local_chat", chat_mock)

    out = asyncio.run(server._serve_local_offline("generate an image of a cat"))
    assert out["trigger"] == "no_floor"
    assert out["degraded"] is True
    assert out["stop_reason"] == "local_skill_no_floor"
    router_mock.assert_not_awaited()
    chat_mock.assert_not_awaited()


def test_serve_local_offline_code_route_unfunded(store, dbpath, monkeypatch):
    """Code route without code floor -> no_floor; health stays healthy."""
    _ready_local_env(store, dbpath, monkeypatch)
    _reset_local_slots()
    asyncio.run(server._catalogs(refresh=True))

    router_mock = AsyncMock(
        return_value={
            "route": "code",
            "brief": "b",
            "router_model": "synth-model-1",
        }
    )
    chat_mock = AsyncMock()
    monkeypatch.setattr(server, "_local_router_floor", router_mock)
    monkeypatch.setattr(server, "_local_chat", chat_mock)

    out = asyncio.run(server._serve_local_offline("implement a parser"))
    assert out["degraded"] is True
    assert out["trigger"] == "no_floor"
    assert out["fallback_reason"] == "local_code_floor_unfunded"
    assert out["stop_reason"] == "local_skill_no_floor"
    chat_mock.assert_not_awaited()

    health = asyncio.run(server._local_op_health())
    assert health.get("healthy") is True


def test_serve_local_offline_code_route_funded(store, dbpath, monkeypatch):
    """Funded code floor + route=code -> specialist role=code, receipts honest."""
    seed_ready(dbpath)
    conn = _open_fk(dbpath)
    try:
        conn.execute(
            "INSERT INTO role_floors "
            "(metric_id, role, family, filled, is_scaffold, floor_value) "
            "VALUES (?, ?, ?, 1, 0, 0.9)",
            ("code:fam:c", "code", "fam"),
        )
        conn.execute(
            "INSERT INTO promote_gates "
            "(cert_id, role, metric_id, status, family, manifest_key, certified_at) "
            "VALUES (?, ?, ?, 'certified', ?, 'promote_gates', datetime('now'))",
            ("cert-code", "code", "code:fam:c", "fam"),
        )
        conn.commit()
    finally:
        conn.close()

    fake = _FakeProbe(
        lpl.ProbeResult(
            runtime_up=True,
            models=(
                lpl.DiscoveredModel(
                    model_id="synth-model-1",
                    raw_name="synth-model-1",
                    runtime="openai_compat",
                ),
            ),
        )
    )
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "LOCAL_RUNTIME_URL", "http://local-runtime.test")
    monkeypatch.setattr(server, "_LOCAL_PROBE_BACKENDS", (fake,))
    monkeypatch.setattr(server, "_CATALOG_CACHE", None)
    _patch_catalog_peers(monkeypatch)
    _reset_local_slots()
    asyncio.run(server._catalogs(refresh=True))

    router_mock = AsyncMock(
        return_value={
            "route": "code",
            "brief": "b",
            "router_model": "synth-model-1",
        }
    )
    chat_mock = AsyncMock(
        return_value={
            "text": "ok",
            "model": "synth-model-1",
            "plane": "local",
            "billing_class": "local_runtime",
            "cost_usd": 0.0,
            "stop_reason": "stop",
        }
    )
    monkeypatch.setattr(server, "_local_router_floor", router_mock)
    monkeypatch.setattr(server, "_local_chat", chat_mock)

    out = asyncio.run(server._serve_local_offline("implement a parser"))
    # m3-task1: contract 6.2 requires degraded=true on successful local serves
    assert out["degraded"] is True
    assert out["trigger"] == "none"
    assert out["floor_role"] == "code"
    code_bind = asyncio.run(store.local_bind("synth-model-1", "code"))
    assert code_bind is not None
    assert out["floor_metric_ids"] == [code_bind["metric_id"]]
    chat_mock.assert_awaited_once()
    kwargs = chat_mock.await_args.kwargs
    assert kwargs.get("role") == "code"
    assert kwargs.get("model_id") == "synth-model-1"
