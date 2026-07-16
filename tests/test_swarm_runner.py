# tests/test_swarm_runner.py
# Runner lifecycle glue: staleness detection (the durable row outlives the
# asyncio task) and effective-status override. Full end-to-end runner drive
# happens at the C4 tools level with a real dry_run.

from datetime import datetime, timedelta


from src.swarm.runner import effective_status, is_stale


def _row(status, updated_delta_sec):
    updated = (datetime.now() - timedelta(seconds=updated_delta_sec)).isoformat()
    return {"status": status, "updated_at": updated}


class TestStaleness:
    def test_fresh_running_row_is_not_stale(self):
        assert is_stale(_row("running", 1), stale_after_sec=300) is False

    def test_old_running_row_is_stale(self):
        assert is_stale(_row("running", 999), stale_after_sec=300) is True

    def test_terminal_rows_are_never_stale(self):
        for status in ("completed", "failed", "cancelled", "stopped_budget"):
            assert is_stale(_row(status, 99999), stale_after_sec=1) is False

    def test_missing_or_bad_timestamp_is_stale(self):
        assert is_stale({"status": "running", "updated_at": ""}, 300) is True
        assert is_stale({"status": "running", "updated_at": "not-a-date"}, 300) is True

    def test_preflight_and_queued_covered(self):
        assert is_stale(_row("preflight", 999), 300) is True
        assert is_stale(_row("queued", 999), 300) is True

    def test_effective_status_overrides_stuck_row(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_SWARM_EVAL_TIMEOUT", "10")  # stale horizon = 30s
        assert effective_status(_row("running", 5)) == "running"
        assert effective_status(_row("running", 100)) == "failed_stale"
        assert effective_status(_row("completed", 100000)) == "completed"
