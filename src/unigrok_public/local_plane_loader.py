"""Local-plane seed load + rewrite-at-load rebind (GemmaGrok harness data path).

Normative six-step loader contract (milestone-1):
  1. Probe/discover is caller-supplied; each model resolves ``family`` via
     ``family_map`` ordered first-match. No hard-coded model ids.
  2. Seed assets (dialects, floors, manifest pins, promote certs, traps) load
     from JSON via :func:`load_seed_assets`; scorecard rows land scaffold-only.
  3. For each discovered model + each role present in DATA for that family:
     rebind floor/cert rows onto ``model_id`` and rewrite ``runtime_binds``.
  4. DATA readiness (``ready_candidate``) requires both min offline roles
     (``router`` and ``text_generator``) filled + fresh-cert bound; probe is
     external and separate.
  5. Single runtime keyspace: invoke-time reads only :func:`get_bind`; missing
     bind means request-scoped ``no_floor`` / fail-closed, never a raw
     scorecard-id fallback.
  6. Re-running :func:`rewrite_at_load` rebuilds ``runtime_binds`` in one
     transaction.

Callers open a fresh sqlite3 connection with PRAGMA foreign_keys=ON; this
module never leaves a failed transaction open.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Protocol, Sequence, runtime_checkable

Role = Literal["router", "text_generator", "judge", "gate", "code", "other"]
PlaneId = Literal["cli", "api", "local"]

# Offline plane-ready min role set (plane-level; not request-scoped role-fit).
MIN_OFFLINE_ROLES: frozenset[str] = frozenset({"router", "text_generator"})

_VALID_ROLES: frozenset[str] = frozenset(
    {"router", "text_generator", "judge", "gate", "code", "other"}
)

# ---------------------------------------------------------------------------
# Reports / value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredModel:
    """One runtime-discovered checkpoint."""

    model_id: str
    raw_name: str
    runtime: str = "other"
    adapters: tuple[str, ...] = ()


@dataclass(frozen=True)
class BindResult:
    model_id: str
    role: str
    family: str
    metric_id: str
    cert_id: Optional[str]


@dataclass(frozen=True)
class LoadReport:
    ok: bool
    binds: tuple[BindResult, ...]
    missing_min_roles: tuple[str, ...]
    errors: tuple[str, ...]
    ready_candidate: bool


@dataclass
class SeedLoadStats:
    family_map: int = 0
    dialect_profiles: int = 0
    gate_manifest: int = 0
    promote: int = 0
    traps: int = 0
    scorecard: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time / transaction helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _in_transaction(conn: sqlite3.Connection) -> bool:
    """True if sqlite3 has an open transaction (non-autocommit or dirty)."""
    try:
        return conn.in_transaction
    except AttributeError:
        return False


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """BEGIN IMMEDIATE only when not already inside a transaction."""
    if not _in_transaction(conn):
        conn.execute("BEGIN IMMEDIATE")


def _as_bool(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    return default


def _as_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# load_seed_assets — tolerant seed ingest
# ---------------------------------------------------------------------------


def load_seed_assets(
    conn: sqlite3.Connection,
    *,
    dialect_matrix_path: Path,
    family_map_path: Path,
    scorecard_path: Path | None,
    gate_manifest_path: Path,
    promote_path: Path,
    traps_path: Path,
) -> SeedLoadStats:
    """Load seed JSON from explicit paths into harness tables (idempotent upserts).

    Accepted shapes:

    family_map.json
        list of ``{"match_kind": "substring"|"regex", "pattern": str,
        "family": str, "priority": int?, "enabled": bool?}``
        (also tolerates ``match`` as alias for ``match_kind``).

    dialect_matrix.json
        either ``{family: {slot: content_str}}`` or a list of
        ``{"family", "slot", "content", "version"?, "source_path"?}``.

    gate_manifest.json
        list of ``{"asset_key", "sha256", "freshness_sla_s", "pinned_at",
        "source_uri"?, "notes"?}``.

    promote.json
        list of ``{"cert_id", "role", "metric_id",
        "status": pending|certified|revoked, "model_id"?, "family"?,
        "ship_fr"?, "manifest_key"?, "certified_at"?, "expires_at"?,
        "payload_json"?}``. Default ``manifest_key`` is ``"promote_gates"``.

    traps.json
        list of ``{"trap_id", "role"?, "fixture_hash", "fixture_uri",
        "version", "status": active|waived|retired, "manifest_key"?,
        "last_pass_at"?, "payload_json"?}``. Default ``manifest_key`` is
        ``"trap_regression"``.

    scorecard.json (historical gemma-smart-floor export)
        list of rows with ``skill|role``, ``family``?, ``model|checkpoint``,
        ``floor|floor_value``?, ``interval_lo``?, ``interval_hi``?,
        ``n|sample_n``?, ``id``?. **Always** inserted with ``is_scaffold=1``,
        ``filled=0``. Original checkpoint/model id goes **only** into
        ``scorecard_src`` — never a live ``model_id`` bind.

    A missing required file appends ``missing_asset:<name>`` to
    ``stats.errors`` but never raises; a missing optional scorecard is
    skipped silently. Uses a single IMMEDIATE transaction.
    """
    stats = SeedLoadStats()
    now = _utc_now_iso()

    try:
        _begin_immediate(conn)

        required: list[tuple[str, Path]] = [
            ("family_map", Path(family_map_path)),
            ("dialect_matrix", Path(dialect_matrix_path)),
            ("gate_manifest", Path(gate_manifest_path)),
            ("promote_gates", Path(promote_path)),
            ("trap_regression", Path(traps_path)),
        ]
        present: dict[str, Path] = {}
        for name, path in required:
            if not path.is_file():
                stats.errors.append(f"missing_asset:{name}")
            else:
                present[name] = path

        if "family_map" in present:
            stats.family_map = _load_family_map(
                conn, _read_json(present["family_map"]), stats.errors
            )
        if "dialect_matrix" in present:
            stats.dialect_profiles = _load_dialect_matrix(
                conn, _read_json(present["dialect_matrix"]), stats.errors, now
            )
        if "gate_manifest" in present:
            stats.gate_manifest = _load_gate_manifest(
                conn, _read_json(present["gate_manifest"]), stats.errors
            )
        if "promote_gates" in present:
            stats.promote = _load_promote(
                conn, _read_json(present["promote_gates"]), stats.errors
            )
        if "trap_regression" in present:
            stats.traps = _load_traps(
                conn, _read_json(present["trap_regression"]), stats.errors
            )

        if scorecard_path is not None and Path(scorecard_path).is_file():
            stats.scorecard = _load_scorecard(
                conn, _read_json(Path(scorecard_path)), stats.errors, now
            )

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        stats.errors.append(f"seed_txn:{e}")
    return stats


def _load_family_map(conn: sqlite3.Connection, data: Any, errors: list[str]) -> int:
    if not isinstance(data, list):
        errors.append("family_map: expected list")
        return 0
    n = 0
    for i, row in enumerate(data):
        if not isinstance(row, Mapping):
            errors.append(f"family_map[{i}]: not object")
            continue
        kind = (row.get("match_kind") or row.get("match") or "").strip().lower()
        if kind not in ("regex", "substring"):
            errors.append(f"family_map[{i}]: bad match_kind")
            continue
        pattern = row.get("pattern")
        family = row.get("family")
        if not pattern or not family:
            errors.append(f"family_map[{i}]: missing pattern/family")
            continue
        priority = _as_int(row.get("priority"), 100)
        enabled = 1 if _as_bool(row.get("enabled"), True) else 0
        conn.execute(
            """
            INSERT INTO family_map (match_kind, pattern, family, priority, enabled)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(match_kind, pattern, family) DO UPDATE SET
              priority=excluded.priority,
              enabled=excluded.enabled
            """,
            (kind, str(pattern), str(family), priority, enabled),
        )
        n += 1
    return n


def _load_dialect_matrix(
    conn: sqlite3.Connection, data: Any, errors: list[str], now: str
) -> int:
    rows: list[tuple[str, str, str, Optional[str], Optional[str]]] = []
    if isinstance(data, Mapping):
        # {family: {slot: content}}
        for family, slots in data.items():
            if not isinstance(slots, Mapping):
                errors.append(f"dialect_matrix[{family}]: expected slot map")
                continue
            for slot, content in slots.items():
                if content is None:
                    continue
                rows.append((str(family), str(slot), str(content), None, None))
    elif isinstance(data, list):
        for i, row in enumerate(data):
            if not isinstance(row, Mapping):
                errors.append(f"dialect_matrix[{i}]: not object")
                continue
            family, slot, content = row.get("family"), row.get("slot"), row.get("content")
            if not family or not slot or content is None:
                errors.append(f"dialect_matrix[{i}]: missing family/slot/content")
                continue
            rows.append(
                (
                    str(family),
                    str(slot),
                    str(content),
                    str(row["version"]) if row.get("version") is not None else None,
                    str(row["source_path"]) if row.get("source_path") else None,
                )
            )
    else:
        errors.append("dialect_matrix: expected object or list")
        return 0

    n = 0
    for family, slot, content, version, source_path in rows:
        conn.execute(
            """
            INSERT INTO dialect_profiles (family, slot, content, version, source_path, updated_at)
            VALUES (?, ?, ?, COALESCE(?, '1'), ?, ?)
            ON CONFLICT(family, slot) DO UPDATE SET
              content=excluded.content,
              version=COALESCE(excluded.version, dialect_profiles.version),
              source_path=COALESCE(excluded.source_path, dialect_profiles.source_path),
              updated_at=excluded.updated_at
            """,
            (family, slot, content, version, source_path, now),
        )
        n += 1
    return n


def _load_gate_manifest(conn: sqlite3.Connection, data: Any, errors: list[str]) -> int:
    if not isinstance(data, list):
        errors.append("gate_manifest: expected list")
        return 0
    n = 0
    for i, row in enumerate(data):
        if not isinstance(row, Mapping):
            errors.append(f"gate_manifest[{i}]: not object")
            continue
        asset_key = row.get("asset_key")
        sha256 = row.get("sha256")
        sla = _as_int(row.get("freshness_sla_s"))
        pinned_at = row.get("pinned_at")
        if not asset_key or not sha256 or sla is None or not pinned_at:
            errors.append(f"gate_manifest[{i}]: missing required fields")
            continue
        conn.execute(
            """
            INSERT INTO gate_manifest
              (asset_key, sha256, freshness_sla_s, pinned_at, source_uri, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_key) DO UPDATE SET
              sha256=excluded.sha256,
              freshness_sla_s=excluded.freshness_sla_s,
              pinned_at=excluded.pinned_at,
              source_uri=excluded.source_uri,
              notes=excluded.notes
            """,
            (
                str(asset_key),
                str(sha256),
                sla,
                str(pinned_at),
                str(row["source_uri"]) if row.get("source_uri") else None,
                str(row["notes"]) if row.get("notes") else None,
            ),
        )
        n += 1
    return n


def _load_promote(conn: sqlite3.Connection, data: Any, errors: list[str]) -> int:
    if not isinstance(data, list):
        errors.append("promote: expected list")
        return 0
    n = 0
    for i, row in enumerate(data):
        if not isinstance(row, Mapping):
            errors.append(f"promote[{i}]: not object")
            continue
        cert_id = row.get("cert_id")
        role = row.get("role")
        metric_id = row.get("metric_id")
        status = (row.get("status") or "").strip().lower()
        if not cert_id or not role or not metric_id or status not in (
            "pending",
            "certified",
            "revoked",
        ):
            errors.append(f"promote[{i}]: missing/invalid required fields")
            continue
        payload = row.get("payload_json")
        if payload is not None and not isinstance(payload, str):
            payload = json.dumps(payload)
        conn.execute(
            """
            INSERT INTO promote_gates
              (cert_id, role, metric_id, status, model_id, family, ship_fr,
               manifest_key, certified_at, expires_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cert_id) DO UPDATE SET
              role=excluded.role,
              metric_id=excluded.metric_id,
              status=excluded.status,
              model_id=excluded.model_id,
              family=excluded.family,
              ship_fr=excluded.ship_fr,
              manifest_key=excluded.manifest_key,
              certified_at=excluded.certified_at,
              expires_at=excluded.expires_at,
              payload_json=excluded.payload_json
            """,
            (
                str(cert_id),
                str(role),
                str(metric_id),
                status,
                str(row["model_id"]) if row.get("model_id") else None,
                str(row["family"]) if row.get("family") else None,
                _as_float(row.get("ship_fr")),
                str(row.get("manifest_key") or "promote_gates"),
                str(row["certified_at"]) if row.get("certified_at") else None,
                str(row["expires_at"]) if row.get("expires_at") else None,
                payload,
            ),
        )
        n += 1
    return n


def _load_traps(conn: sqlite3.Connection, data: Any, errors: list[str]) -> int:
    if not isinstance(data, list):
        errors.append("traps: expected list")
        return 0
    n = 0
    for i, row in enumerate(data):
        if not isinstance(row, Mapping):
            errors.append(f"traps[{i}]: not object")
            continue
        trap_id = row.get("trap_id")
        fixture_hash = row.get("fixture_hash")
        fixture_uri = row.get("fixture_uri")
        version = row.get("version")
        status = (row.get("status") or "").strip().lower()
        if not trap_id or not fixture_hash or not fixture_uri or not version:
            errors.append(f"traps[{i}]: missing required fields")
            continue
        if status not in ("active", "waived", "retired"):
            errors.append(f"traps[{i}]: bad status")
            continue
        payload = row.get("payload_json")
        if payload is not None and not isinstance(payload, str):
            payload = json.dumps(payload)
        conn.execute(
            """
            INSERT INTO trap_regression
              (trap_id, role, fixture_hash, fixture_uri, version, status,
               manifest_key, last_pass_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trap_id) DO UPDATE SET
              role=excluded.role,
              fixture_hash=excluded.fixture_hash,
              fixture_uri=excluded.fixture_uri,
              version=excluded.version,
              status=excluded.status,
              manifest_key=excluded.manifest_key,
              last_pass_at=excluded.last_pass_at,
              payload_json=excluded.payload_json
            """,
            (
                str(trap_id),
                str(row["role"]) if row.get("role") else None,
                str(fixture_hash),
                str(fixture_uri),
                str(version),
                status,
                str(row.get("manifest_key") or "trap_regression"),
                str(row["last_pass_at"]) if row.get("last_pass_at") else None,
                payload,
            ),
        )
        n += 1
    return n


_HISTORICAL_ROLE_MAP: dict[str, str] = {
    "router": "router",
    "generator": "text_generator",
    "text generator": "text_generator",
    "text_generator": "text_generator",
    "judge": "judge",
    "gate": "gate",
    "code": "code",
}


def _normalize_role(raw: Any) -> str:
    return _HISTORICAL_ROLE_MAP.get(str(raw or "").strip().lower(), "other")


def _load_scorecard(
    conn: sqlite3.Connection, data: Any, errors: list[str], now: str
) -> int:
    """Historical floors: always scaffold, never filled, never live model bind."""
    if not isinstance(data, list):
        errors.append("scorecard: expected list")
        return 0
    n = 0
    for i, row in enumerate(data):
        if not isinstance(row, Mapping):
            errors.append(f"scorecard[{i}]: not object")
            continue
        raw_role = row.get("role") or row.get("skill")
        model_src = row.get("model") or row.get("checkpoint")
        if not raw_role:
            errors.append(f"scorecard[{i}]: missing role/skill")
            continue
        role = _normalize_role(raw_role)
        floor_value = row.get("floor_value")
        if floor_value is None:
            floor_value = row.get("floor")
        floor_value = _as_float(floor_value)
        family = str(row["family"]) if row.get("family") else None
        # Role-scoped synthetic key; original checkpoint id lands ONLY in
        # scorecard_src for audit, never as a runtime lookup key.
        import hashlib

        seed = f"{raw_role}|{model_src or i}|{row.get('id') or ''}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        metric_id = f"{role}:{family or 'unknown'}:{digest}"
        conn.execute(
            """
            INSERT INTO role_floors
              (metric_id, role, family, model_id, floor_value, interval_lo,
               interval_hi, sample_n, is_scaffold, filled, scorecard_src, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?, COALESCE(?, 0), 1, 0, ?, ?)
            ON CONFLICT(metric_id) DO UPDATE SET
              role=excluded.role,
              family=COALESCE(excluded.family, role_floors.family),
              floor_value=COALESCE(excluded.floor_value, role_floors.floor_value),
              interval_lo=COALESCE(excluded.interval_lo, role_floors.interval_lo),
              interval_hi=COALESCE(excluded.interval_hi, role_floors.interval_hi),
              sample_n=COALESCE(excluded.sample_n, role_floors.sample_n),
              scorecard_src=COALESCE(excluded.scorecard_src, role_floors.scorecard_src),
              updated_at=excluded.updated_at
            """,
            (
                metric_id,
                role,
                family,
                floor_value,
                _as_float(row.get("interval_lo")),
                _as_float(row.get("interval_hi")),
                _as_int(row.get("sample_n") if row.get("sample_n") is not None else row.get("n")),
                str(model_src) if model_src else None,
                now,
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Family / metric / cert resolution (rewrite-at-load helpers)
# ---------------------------------------------------------------------------


def resolve_family(
    conn: sqlite3.Connection, model_id: str, raw_name: str
) -> Optional[str]:
    """Ordered first-match against family_map (enabled, priority ASC, id ASC)."""
    rows = conn.execute(
        """
        SELECT match_kind, pattern, family
        FROM family_map
        WHERE enabled = 1
        ORDER BY priority ASC, id ASC
        """
    ).fetchall()
    hay = raw_name or model_id
    for row in rows:
        match_kind, pattern, family = row[0], row[1], row[2]
        if match_kind == "substring":
            lowered = str(pattern).lower()
            if lowered in str(model_id).lower() or lowered in str(hay).lower():
                return str(family)
        elif match_kind == "regex":
            try:
                if re.search(pattern, model_id) or re.search(pattern, hay):
                    return str(family)
            except re.error:
                continue
    return None


def roles_for_family(conn: sqlite3.Connection, family: str) -> list[str]:
    """Roles that have a role_floors metric row for this family (or family-null)."""
    rows = conn.execute(
        """
        SELECT DISTINCT role FROM role_floors
        WHERE family = ? OR family IS NULL
        ORDER BY role
        """,
        (family,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def pick_role_metric(
    conn: sqlite3.Connection, family: str, role: str
) -> Optional[str]:
    """Pick the role-scoped metric_id for (family, role).

    Prefer filled (measured, non-scaffold) rows with the highest floor over
    scaffold rows; prefer family-exact over family-null; stable ORDER BY
    metric_id for determinism.
    """
    row = conn.execute(
        """
        SELECT metric_id FROM role_floors
        WHERE role = ? AND (family = ? OR family IS NULL)
        ORDER BY
          CASE WHEN filled = 1 AND is_scaffold = 0 THEN 0 ELSE 1 END,
          COALESCE(floor_value, -1e18) DESC,
          CASE WHEN family = ? THEN 0 ELSE 1 END,
          metric_id ASC
        LIMIT 1
        """,
        (role, family, family),
    ).fetchone()
    return str(row[0]) if row else None


def pick_cert(
    conn: sqlite3.Connection,
    role: str,
    family: Optional[str],
    metric_id: Optional[str] = None,
) -> Optional[str]:
    """Return cert_id of the best certified promote_gates row, or None.

    Temporarily uses sqlite3.Row without permanently mutating conn.row_factory.
    """
    prior = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        if metric_id:
            cur = conn.execute(
                """
                SELECT cert_id FROM promote_gates
                 WHERE role = ?
                   AND metric_id = ?
                   AND status = 'certified'
                   AND (family IS NULL OR family = ?)
                 ORDER BY
                   CASE WHEN family = ? THEN 0 ELSE 1 END,
                   certified_at DESC,
                   cert_id ASC
                 LIMIT 1
                """,
                (role, metric_id, family, family),
            )
        else:
            cur = conn.execute(
                """
                SELECT cert_id FROM promote_gates
                 WHERE role = ?
                   AND status = 'certified'
                   AND (family IS NULL OR family = ?)
                 ORDER BY
                   CASE WHEN family = ? THEN 0 ELSE 1 END,
                   certified_at DESC,
                   cert_id ASC
                 LIMIT 1
                """,
                (role, family, family),
            )
        row = cur.fetchone()
        if row is None:
            return None
        return str(row["cert_id"])
    finally:
        conn.row_factory = prior


def cert_fresh(
    conn: sqlite3.Connection, cert_id: Optional[str], *, now: float | None = None
) -> bool:
    """True when promote_gates row is certified, unexpired, and its
    gate_manifest pin is within the freshness SLA."""
    if not cert_id:
        return False
    prior = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT pg.status,
                   pg.expires_at,
                   pg.manifest_key,
                   gm.sha256,
                   gm.freshness_sla_s,
                   gm.pinned_at
              FROM promote_gates pg
              LEFT JOIN gate_manifest gm ON gm.asset_key = pg.manifest_key
             WHERE pg.cert_id = ?
            """,
            (cert_id,),
        ).fetchone()
    finally:
        conn.row_factory = prior
    if row is None:
        return False
    if str(row["status"]) != "certified":
        return False
    ts_now = time.time() if now is None else float(now)
    expires = _parse_iso(row["expires_at"])
    if expires is not None and expires.timestamp() < ts_now:
        return False
    if row["sha256"] is None or row["freshness_sla_s"] is None:
        return False
    pinned = _parse_iso(row["pinned_at"])
    if pinned is None:
        return False
    age = max(0.0, ts_now - pinned.timestamp())
    return age <= float(row["freshness_sla_s"])


def plane_data_ready(conn: sqlite3.Connection) -> bool:
    """DATA-plane ready: v_local_min_role_cert has role_ready=1 for both
    router and text_generator (>=1 model each; may differ)."""
    try:
        rows = conn.execute(
            """
            SELECT role, COUNT(*) AS n
              FROM v_local_min_role_cert
             WHERE role_ready = 1
               AND role IN ('router', 'text_generator')
             GROUP BY role
            """
        ).fetchall()
    except sqlite3.Error:
        return False
    counts: dict[str, int] = {}
    for r in rows:
        role = r[0] if not hasattr(r, "keys") else r["role"]
        n = r[1] if not hasattr(r, "keys") else r["n"]
        counts[str(role)] = int(n)
    return counts.get("router", 0) >= 1 and counts.get("text_generator", 0) >= 1


def get_bind(conn: sqlite3.Connection, model_id: str, role: str) -> BindResult | None:
    """Read ONLY runtime_binds; never fall back to scorecard / raw dual-key ids."""
    if not model_id or not role:
        return None
    prior = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT model_id, role, family, metric_id, cert_id
              FROM runtime_binds
             WHERE model_id = ? AND role = ?
            """,
            (model_id, role),
        ).fetchone()
        if row is None:
            return None
        return BindResult(
            model_id=str(row["model_id"]),
            role=str(row["role"]),
            family=str(row["family"]),
            metric_id=str(row["metric_id"]),
            cert_id=str(row["cert_id"]) if row["cert_id"] is not None else None,
        )
    finally:
        conn.row_factory = prior


def list_binds(
    conn: sqlite3.Connection,
    *,
    model_id: str | None = None,
    role: str | None = None,
) -> list[BindResult]:
    """List runtime_binds rows. Optional filters; never scorecard/raw-key fallback."""
    clauses: list[str] = []
    params: list[Any] = []
    if model_id is not None:
        clauses.append("model_id = ?")
        params.append(model_id)
    if role is not None:
        clauses.append("role = ?")
        params.append(role)
    sql = (
        "SELECT model_id, role, family, metric_id, cert_id "
        "FROM runtime_binds"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY model_id, role"
    rows = conn.execute(sql, params).fetchall()
    out: list[BindResult] = []
    for row in rows:
        out.append(
            BindResult(
                model_id=str(row[0]),
                role=str(row[1]),
                family=str(row[2]),
                metric_id=str(row[3]),
                cert_id=str(row[4]) if row[4] is not None else None,
            )
        )
    return out


def get_knob(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """Read local_plane_knobs.value_json; return default on missing or parse error."""
    try:
        row = conn.execute(
            "SELECT value_json FROM local_plane_knobs WHERE key = ?",
            (key,),
        ).fetchone()
    except sqlite3.Error:
        return default
    if row is None:
        return default
    raw = row[0] if not hasattr(row, "keys") else row["value_json"]
    if raw is None:
        return default
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# rewrite_at_load — single runtime keyspace, fail-closed min roles
# ---------------------------------------------------------------------------


def rewrite_at_load(
    conn: sqlite3.Connection,
    discovered: Sequence[DiscoveredModel],
    *,
    now_iso: Optional[str] = None,
) -> LoadReport:
    """Rebind role_floors + runtime_binds from a fresh discovery snapshot.

    **Rationale (rewrite-at-load, not dual-key):** role-scoped metric ids are
    stable keys in ``role_floors`` / promote / traps; the *model* that
    currently occupies a role is ephemeral discovery state. Dual-keying
    (metric_id x model_id) would fork floors and certs per checkpoint and
    break promote-law continuity across swaps. Each load clears
    ``runtime_binds``, re-resolves family -> roles -> metric -> cert for every
    discovered model, and writes the live ``model_id`` onto the existing
    metric row. Historical scorecard checkpoints stay in ``scorecard_src``
    with ``filled=0``.
    """
    errors: list[str] = []
    binds: list[BindResult] = []
    now = now_iso or _utc_now_iso()
    now_dt = _parse_iso(now) or datetime.now(timezone.utc)
    ts_now = now_dt.timestamp()

    try:
        _begin_immediate(conn)
        conn.execute("DELETE FROM runtime_binds")

        for m in discovered:
            family = resolve_family(conn, m.model_id, m.raw_name)
            if not family:
                errors.append(f"no_family:{m.model_id}")
                continue
            roles = roles_for_family(conn, family)
            if not roles:
                errors.append(f"no_roles:{family}:{m.model_id}")
                continue
            for role in roles:
                if role not in _VALID_ROLES:
                    continue
                metric = pick_role_metric(conn, family, role)
                if metric is None:
                    errors.append(f"no_metric:{family}:{role}")
                    continue
                cert_id = pick_cert(conn, role, family, metric)
                conn.execute(
                    """
                    UPDATE role_floors
                    SET model_id = ?,
                        family = ?,
                        updated_at = COALESCE(?, updated_at)
                    WHERE metric_id = ?
                    """,
                    (m.model_id, family, now, metric),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_binds
                      (model_id, role, family, metric_id, dialect_family, cert_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (m.model_id, role, family, metric, family, cert_id),
                )
                binds.append(BindResult(m.model_id, role, family, metric, cert_id))

        missing = tuple(
            r
            for r in sorted(MIN_OFFLINE_ROLES)
            if not any(
                b.role == r and cert_fresh(conn, b.cert_id, now=ts_now)
                for b in binds
            )
        )
        ready = len(missing) == 0 and plane_data_ready(conn)
        conn.execute("COMMIT")
        return LoadReport(
            ok=(not errors) or ready,
            binds=tuple(binds),
            missing_min_roles=missing,
            errors=tuple(errors),
            ready_candidate=ready,
        )
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return LoadReport(
            ok=False,
            binds=(),
            missing_min_roles=tuple(sorted(MIN_OFFLINE_ROLES)),
            errors=(str(e),),
            ready_candidate=False,
        )


# ---------------------------------------------------------------------------
# Runtime-agnostic probe interface (M2-T1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Pure-python result of probing a local runtime for discovered models."""

    runtime_up: bool
    models: tuple[DiscoveredModel, ...] = ()
    errors: tuple[str, ...] = ()


@runtime_checkable
class ProbeBackend(Protocol):
    """Pluggable runtime probe shape. MLX / Ollama / OpenAI-compat are examples."""

    name: str

    async def list_models(self, base_url: str, timeout: float) -> ProbeResult:
        """Probe ``base_url``; never raises — return ``runtime_up=False`` on failure."""
        ...


class OpenAICompatProbe:
    """GET ``{base}/v1/models`` — OpenAI-shaped ``{"data":[{"id":...}]}``."""

    name = "openai_compat"

    async def list_models(self, base_url: str, timeout: float) -> ProbeResult:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - import env
            return ProbeResult(runtime_up=False, errors=(f"openai_compat:httpx:{exc}",))
        base = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{base}/v1/models")
                resp.raise_for_status()
                payload = resp.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                return ProbeResult(
                    runtime_up=False, errors=("openai_compat:openai_models_shape",)
                )
            models: list[DiscoveredModel] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mid = row.get("id")
                if not mid:
                    continue
                mid_s = str(mid)
                models.append(
                    DiscoveredModel(
                        model_id=mid_s,
                        raw_name=mid_s,
                        runtime="openai_compat",
                    )
                )
            return ProbeResult(runtime_up=True, models=tuple(models))
        except Exception as exc:
            return ProbeResult(runtime_up=False, errors=(f"openai_compat:{exc}",))


class OllamaProbe:
    """GET ``{base}/api/tags`` — Ollama-shaped ``{"models":[{"name"|"model":...}]}``."""

    name = "ollama"

    async def list_models(self, base_url: str, timeout: float) -> ProbeResult:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - import env
            return ProbeResult(runtime_up=False, errors=(f"ollama:httpx:{exc}",))
        base = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{base}/api/tags")
                resp.raise_for_status()
                payload = resp.json()
            rows = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                return ProbeResult(
                    runtime_up=False, errors=("ollama:ollama_tags_shape",)
                )
            models: list[DiscoveredModel] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("name") or row.get("model")
                if not name:
                    continue
                name_s = str(name)
                models.append(
                    DiscoveredModel(
                        model_id=name_s,
                        raw_name=name_s,
                        runtime="ollama",
                    )
                )
            return ProbeResult(runtime_up=True, models=tuple(models))
        except Exception as exc:
            return ProbeResult(runtime_up=False, errors=(f"ollama:{exc}",))


class MLXProbe:
    """Example second backend: OpenAI-compat ``/v1/models`` path, ``runtime="mlx"``.

    Not in ``DEFAULT_PROBE_BACKENDS`` — proves the shape is brand-agnostic.
    """

    name = "mlx"

    async def list_models(self, base_url: str, timeout: float) -> ProbeResult:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - import env
            return ProbeResult(runtime_up=False, errors=(f"mlx:httpx:{exc}",))
        base = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{base}/v1/models")
                resp.raise_for_status()
                payload = resp.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                return ProbeResult(runtime_up=False, errors=("mlx:openai_models_shape",))
            models: list[DiscoveredModel] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mid = row.get("id")
                if not mid:
                    continue
                mid_s = str(mid)
                models.append(
                    DiscoveredModel(
                        model_id=mid_s,
                        raw_name=mid_s,
                        runtime="mlx",
                    )
                )
            return ProbeResult(runtime_up=True, models=tuple(models))
        except Exception as exc:
            return ProbeResult(runtime_up=False, errors=(f"mlx:{exc}",))


DEFAULT_PROBE_BACKENDS: tuple[ProbeBackend, ...] = (
    OpenAICompatProbe(),
    OllamaProbe(),
)


async def probe_runtime(
    base_url: str,
    *,
    timeout: float,
    backends: Sequence[ProbeBackend] | None = None,
) -> ProbeResult:
    """Try backends in order; first ``runtime_up=True`` wins (its models/tags kept).

    If none are up, return ``runtime_up=False`` with accumulated errors.
    """
    chain: Sequence[ProbeBackend] = (
        backends if backends is not None else DEFAULT_PROBE_BACKENDS
    )
    errors: list[str] = []
    for backend in chain:
        result = await backend.list_models(base_url, timeout)
        if result.runtime_up:
            return result
        errors.extend(result.errors)
    return ProbeResult(runtime_up=False, errors=tuple(errors))
