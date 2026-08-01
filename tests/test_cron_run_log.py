from __future__ import annotations

from src.db import cron_run_log


class _FakeConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self

    def commit(self):
        self.commits += 1


def test_running_increments_attempt_and_final_status_does_not(monkeypatch):
    fake = _FakeConnection()
    monkeypatch.setattr(cron_run_log, "db_connect", lambda: fake)

    cron_run_log.record_cron_run("detail", "2026-08-02", "running")
    cron_run_log.record_cron_run("detail", "2026-08-02", "success", detail="ok")

    assert "run_count = task_runs.run_count + 1" in fake.calls[0][0]
    assert "run_count = task_runs.run_count + 1" not in fake.calls[1][0]
    assert fake.calls[1][1][2] == "success"
    assert fake.commits == 2


def test_unknown_status_is_rejected():
    try:
        cron_run_log.record_cron_run("detail", "2026-08-02", "unknown")
    except ValueError as exc:
        assert "unsupported cron status" in str(exc)
    else:
        raise AssertionError("unknown status must fail")
