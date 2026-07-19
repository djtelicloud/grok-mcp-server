"""Near-physics envelopes — do not tune from telemetry.

These are resource / causality / non-exfil ceilings. Cognition posteriors and
operational defaults live elsewhere (governor weight bundles, env knobs under
these caps). See docs/DEOVERFIT.md.
"""

from __future__ import annotations

# Causality: stale writers must not commit.
REQUIRE_LEASE_GENERATION_ON_CAS = True
REQUIRE_VERIFYING_BEFORE_COMPLETE = True

# Resource ceilings (hard). Operational defaults may be lower.
MAX_MISSION_WALL_SECONDS = 86_400
MAX_ARTIFACT_PROJECTION_BYTES = 100_000
MAX_ACCEPTANCE_CHARS = 20_000
MAX_EVIDENCE_RECORDS = 256

# Non-exfil: projections and dual-logs must never carry raw secrets.
REQUIRE_REDACTED_PROJECTIONS = True
REQUIRE_REDACTED_DECISION_LOGS = True

# Public boundary (CLI child).
CLI_CHILD_LOCAL_SHELL = False
CLI_CHILD_HOST_FS = False

# Promotion.
REQUIRE_INDEPENDENT_EVIDENCE_FOR_PROMOTE = True
NEEDLE_RUNTIME_DEFAULT = False  # visibly inactive until real promotion path
