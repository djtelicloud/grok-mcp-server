import sqlite3
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

class AttemptEvent:
    def __init__(self, work_item_id: str, event_type: str, role: str, root_reference: Optional[str] = None, variant_key: Optional[str] = None):
        self.work_item_id = work_item_id
        self.event_type = event_type
        self.role = role
        self.root_reference = root_reference
        self.variant_key = variant_key
        self.timestamp = datetime.now(timezone.utc).isoformat()

class AttemptLedger:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.execute("PRAGMA synchronous = FULL")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        with conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS attempt_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_item_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    root_reference TEXT,
                    variant_key TEXT,
                    timestamp TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_work_item ON attempt_events(work_item_id)')

    def log_started(self, role: str, root_reference: Optional[str] = None, variant_key: Optional[str] = None) -> str:
        """Log a started event before transport invocation."""
        work_item_id = str(uuid.uuid4())
        conn = self._get_conn()
        with conn:
            conn.execute(
                "INSERT INTO attempt_events (work_item_id, event_type, role, root_reference, variant_key, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (work_item_id, "started", role, root_reference, variant_key, datetime.now(timezone.utc).isoformat())
            )
        return work_item_id

    def log_completed(self, work_item_id: str):
        """Log a completed event after successful execution and mechanical validation."""
        conn = self._get_conn()
        with conn:
            # We don't really need to select the previous event, we just append a completed event
            # But let's copy the metadata for easy querying
            row = conn.execute("SELECT role, root_reference, variant_key FROM attempt_events WHERE work_item_id = ? AND event_type = 'started'", (work_item_id,)).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO attempt_events (work_item_id, event_type, role, root_reference, variant_key, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (work_item_id, "completed", row["role"], row["root_reference"], row["variant_key"], datetime.now(timezone.utc).isoformat())
                )

    def log_failed(self, work_item_id: str):
        """Log a failed event (e.g. timeout, validation error)."""
        conn = self._get_conn()
        with conn:
            row = conn.execute("SELECT role, root_reference, variant_key FROM attempt_events WHERE work_item_id = ? AND event_type = 'started'", (work_item_id,)).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO attempt_events (work_item_id, event_type, role, root_reference, variant_key, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (work_item_id, "failed", row["role"], row["root_reference"], row["variant_key"], datetime.now(timezone.utc).isoformat())
                )

    def get_total_attempts(self) -> int:
        """Returns the total number of distinct work items (started events)."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(DISTINCT work_item_id) as cnt FROM attempt_events WHERE event_type = 'started'").fetchone()
        return row["cnt"] if row else 0

    def get_indeterminate_attempts(self) -> List[str]:
        """Returns work_item_ids that started but never completed or failed."""
        conn = self._get_conn()
        rows = conn.execute('''
            SELECT work_item_id
            FROM attempt_events
            GROUP BY work_item_id
            HAVING SUM(CASE WHEN event_type IN ('completed', 'failed') THEN 1 ELSE 0 END) = 0
        ''').fetchall()
        return [row["work_item_id"] for row in rows]
