"""Append-only, resumable attempt ledger.

Every attempt — success, failure, timeout, empty response, invalid schema,
judge disagreement, quarantine — becomes one immutable JSONL row keyed by
its deterministic work key. Resuming a harvest replays the ledger first and
skips any work key that already reached a terminal state, so a resume can
never duplicate a provider call or an effect.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "needle-harvest-ledger/v1"

TERMINAL_STATUSES = frozenset(
    {
        "ACCEPTED",
        "REJECTED",
        "QUARANTINED",
        "EXPIRED",
        "BUDGET_EXHAUSTED",
        "TRANSPORT_FAILURE",
    }
)


class AttemptLedger:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: list[dict[str, Any]] = []
        if self._path.exists():
            with open(self._path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        self._rows.append(json.loads(line))

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        work_key: str,
        attempt_id: str,
        effect_id: str,
        status: str,
        transport_status: str = "",
        proposal_verdict: str = "",
        episode_outcome: str = "",
        detail: str = "",
        provider_receipt: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        row = {
            "schema": LEDGER_SCHEMA,
            "sequence": len(self._rows),
            "work_key": work_key,
            "attempt_id": attempt_id,
            "effect_id": effect_id,
            "status": status,
            "transport_status": transport_status,
            "proposal_verdict": proposal_verdict,
            "episode_outcome": episode_outcome,
            "detail": detail,
            "provider_receipt": provider_receipt or {},
        }
        # Append-only: open in "a" every time; rows are never rewritten.
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        self._rows.append(row)
        return row

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def completed_work_keys(self) -> set[str]:
        """Work keys that reached a terminal state — skipped on resume."""
        return {
            row["work_key"]
            for row in self._rows
            if row.get("status") in TERMINAL_STATUSES
        }

    def effect_ids_by_work_key(self) -> dict[str, str]:
        return {
            row["work_key"]: row["effect_id"]
            for row in self._rows
            if row.get("effect_id")
        }
