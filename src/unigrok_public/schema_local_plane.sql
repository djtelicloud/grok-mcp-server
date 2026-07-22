-- milestone-1 local-plane DATA (role-scoped after rewrite-at-load)
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- family_map: ordered first-match rules (not code allowlists)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_map (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  match_kind    TEXT NOT NULL CHECK (match_kind IN ('regex', 'substring')),
  pattern       TEXT NOT NULL,
  family        TEXT NOT NULL,
  priority      INTEGER NOT NULL DEFAULT 100,  -- lower = earlier match
  enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  UNIQUE (match_kind, pattern, family)
);
CREATE INDEX IF NOT EXISTS idx_family_map_priority ON family_map(enabled, priority);

-- ---------------------------------------------------------------------------
-- dialect_profiles: per-family slot content from dialect_matrix.json
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dialect_profiles (
  family        TEXT NOT NULL,
  slot          TEXT NOT NULL,   -- e.g. system_lock, router, generator
  content       TEXT NOT NULL,
  version       TEXT NOT NULL DEFAULT '1',
  source_path   TEXT,            -- provenance (matrix path / seed id)
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (family, slot)
);

-- ---------------------------------------------------------------------------
-- role_floors: role-scoped metrics after rewrite-at-load
-- scaffold rows never satisfy ready / filled_floor_cert
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS role_floors (
  metric_id     TEXT PRIMARY KEY,     -- role-scoped id only (post-rewrite)
  role          TEXT NOT NULL CHECK (
                  role IN ('router', 'text_generator', 'judge', 'gate', 'code', 'other')
                ),
  model_id      TEXT,                 -- runtime bind after rewrite; NULL until bound
  family        TEXT,
  filled        INTEGER NOT NULL DEFAULT 0 CHECK (filled IN (0, 1)),
  is_scaffold   INTEGER NOT NULL DEFAULT 1 CHECK (is_scaffold IN (0, 1)),
  floor_value   REAL,                 -- measured floor; NULL if scaffold
  interval_lo   REAL,                 -- Wilson (or seed) lower
  interval_hi   REAL,
  sample_n      INTEGER NOT NULL DEFAULT 0,
  scorecard_src TEXT,                 -- prior scorecard row id / path (audit only)
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (NOT (filled = 1 AND is_scaffold = 1)),
  CHECK (NOT (filled = 1 AND floor_value IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_role_floors_role_filled
  ON role_floors(role, filled, is_scaffold);
CREATE INDEX IF NOT EXISTS idx_role_floors_model
  ON role_floors(model_id) WHERE model_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- gate_manifest: SHA pins + freshness SLA for promote / trap assets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gate_manifest (
  asset_key     TEXT PRIMARY KEY,     -- e.g. promote_gates, trap_regression, dialect_matrix
  sha256        TEXT NOT NULL,
  freshness_sla_s INTEGER NOT NULL,  -- max age before cert considered stale
  pinned_at     TEXT NOT NULL,        -- when pin was written
  source_uri    TEXT,
  notes         TEXT
);

-- ---------------------------------------------------------------------------
-- promote_gates: promote-law certs (DATA; gate ready for min roles)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS promote_gates (
  cert_id       TEXT PRIMARY KEY,
  role          TEXT NOT NULL,
  model_id      TEXT,                 -- bound at rewrite-at-load
  family        TEXT,
  metric_id     TEXT NOT NULL,        -- FK-ish to role_floors.metric_id
  status        TEXT NOT NULL CHECK (status IN ('pending', 'certified', 'revoked')),
  ship_fr       REAL,                 -- promote score / ship-FR if present
  manifest_key  TEXT NOT NULL DEFAULT 'promote_gates',
  certified_at  TEXT,
  expires_at    TEXT,                 -- optional hard expiry
  payload_json  TEXT,                 -- opaque promote evidence
  FOREIGN KEY (manifest_key) REFERENCES gate_manifest(asset_key)
);
CREATE INDEX IF NOT EXISTS idx_promote_role_status
  ON promote_gates(role, status, model_id);

-- ---------------------------------------------------------------------------
-- trap_regression: versioned trap fixtures + hash pins
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trap_regression (
  trap_id       TEXT PRIMARY KEY,
  role          TEXT,                 -- optional role scope
  fixture_hash  TEXT NOT NULL,        -- sha256 of fixture body
  fixture_uri   TEXT NOT NULL,        -- path or logical id
  version       TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('active', 'waived', 'retired')),
  manifest_key  TEXT NOT NULL DEFAULT 'trap_regression',
  last_pass_at  TEXT,
  payload_json  TEXT,
  FOREIGN KEY (manifest_key) REFERENCES gate_manifest(asset_key)
);
CREATE INDEX IF NOT EXISTS idx_trap_status ON trap_regression(status, role);

-- ---------------------------------------------------------------------------
-- runtime_binds: post-load single keyspace (no dual-key residual)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runtime_binds (
  model_id      TEXT NOT NULL,
  role          TEXT NOT NULL,
  family        TEXT NOT NULL,
  metric_id     TEXT NOT NULL,
  dialect_family TEXT NOT NULL,
  cert_id       TEXT,
  bound_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (model_id, role),
  FOREIGN KEY (metric_id) REFERENCES role_floors(metric_id)
);
CREATE INDEX IF NOT EXISTS idx_runtime_binds_role ON runtime_binds(role);

-- ---------------------------------------------------------------------------
-- local_plane_knobs: concurrency / breaker / continue (config, not magic)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS local_plane_knobs (
  key           TEXT PRIMARY KEY,
  value_json    TEXT NOT NULL,
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Filled-floor cert predicate for plane-level min roles {router, text_generator}
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_local_min_role_cert AS
SELECT
  rb.role,
  rb.model_id,
  rf.filled AS floor_filled,
  rf.is_scaffold,
  pg.status AS cert_status,
  gm.sha256 AS manifest_sha,
  gm.pinned_at,
  gm.freshness_sla_s,
  CASE
    WHEN rf.filled = 1
     AND rf.is_scaffold = 0
     AND pg.status = 'certified'
     AND gm.sha256 IS NOT NULL
     AND (strftime('%s','now') - strftime('%s', gm.pinned_at)) <= gm.freshness_sla_s
     AND (pg.expires_at IS NULL OR pg.expires_at > datetime('now'))
    THEN 1 ELSE 0
  END AS role_ready
FROM runtime_binds rb
JOIN role_floors rf ON rf.metric_id = rb.metric_id
LEFT JOIN promote_gates pg ON pg.cert_id = rb.cert_id
LEFT JOIN gate_manifest gm ON gm.asset_key = pg.manifest_key
WHERE rb.role IN ('router', 'text_generator');
