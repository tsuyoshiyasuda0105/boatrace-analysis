from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

from src.collectors import original_exhibition
from src.db import cron_runtime


JST = ZoneInfo("Asia/Tokyo")


def _task_row(conn, task="job"):
    return conn.execute(
        """
        SELECT status, run_count, started_at, finished_at, success_at, detail
          FROM task_runs WHERE task_name=? AND run_date='2026-08-14'
        """,
        (task,),
    ).fetchone()


def test_record_task_run_preserves_attempt_start_on_success(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cron_runtime.ensure_task_runs_table(conn)
    times = iter(["2026-08-14T08:00:00", "2026-08-14T08:05:00"])
    monkeypatch.setattr(cron_runtime, "_now_iso", lambda: next(times))

    cron_runtime.record_task_run(
        conn, "job", "2026-08-14", "running", increment=True
    )
    cron_runtime.record_task_run(
        conn, "job", "2026-08-14", "success", detail="ok", increment=True
    )

    assert _task_row(conn) == (
        "success",
        1,
        "2026-08-14T08:00:00",
        "2026-08-14T08:05:00",
        "2026-08-14T08:05:00",
        "ok",
    )


def test_skipped_transition_does_not_destroy_running_row(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cron_runtime.ensure_task_runs_table(conn)
    times = iter(["2026-08-14T08:00:00", "2026-08-14T08:01:00"])
    monkeypatch.setattr(cron_runtime, "_now_iso", lambda: next(times))

    cron_runtime.record_task_run(
        conn, "job", "2026-08-14", "running", detail="active", increment=True
    )
    cron_runtime.record_task_run(
        conn, "job", "2026-08-14", "skipped", detail="overlap"
    )

    assert _task_row(conn) == (
        "running",
        1,
        "2026-08-14T08:00:00",
        None,
        None,
        "active",
    )


class _PgLockConnection:
    _kind = "postgres"

    def __init__(self, acquired=True):
        self.acquired = acquired
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return (self.acquired,)


def test_advisory_lock_acquires_and_releases_same_name():
    conn = _PgLockConnection()

    with cron_runtime.advisory_lock(conn, "cron-lock") as locked:
        assert locked is True

    assert "pg_try_advisory_lock" in conn.calls[0][0]
    assert "pg_advisory_unlock" in conn.calls[1][0]
    assert conn.calls[0][1] == conn.calls[1][1] == ("cron-lock",)


def test_parse_race_close_jst_handles_time_and_invalid_values():
    parsed = cron_runtime.parse_race_close_jst("14:35", "2026-08-14")

    assert parsed == datetime(2026, 8, 14, 14, 35, tzinfo=JST)
    assert cron_runtime.parse_race_close_jst("invalid", "2026-08-14") is None


def test_original_exhibition_requires_six_complete_metric_rows(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT,
          stadium_number INTEGER,
          race_number INTEGER,
          race_closed_at TEXT
        );
        CREATE TABLE race_original_exhibitions (
          race_id TEXT,
          boat_number INTEGER,
          lap_time REAL,
          turn_time REAL,
          straight_time REAL
        );
        """
    )
    for race_no in (1, 2, 3, 4):
        conn.execute(
            "INSERT INTO races VALUES (?, '2026-08-14', 1, ?, '12:00')",
            (f"race-{race_no}", race_no),
        )
    for boat in range(1, 7):
        conn.execute(
            "INSERT INTO race_original_exhibitions VALUES ('race-1', ?, 1, 1, 1)",
            (boat,),
        )
        conn.execute(
            "INSERT INTO race_original_exhibitions VALUES ('race-3', ?, 0, 0, 0)",
            (boat,),
        )
        conn.execute(
            "INSERT INTO race_original_exhibitions VALUES ('race-4', ?, 1, NULL, 1)",
            (boat,),
        )
    for boat in range(1, 4):
        conn.execute(
            "INSERT INTO race_original_exhibitions VALUES ('race-2', ?, 1, 1, 1)",
            (boat,),
        )
    conn.commit()
    monkeypatch.setattr(cron_runtime, "db_connect", lambda: conn)
    monkeypatch.setattr(original_exhibition, "SOURCE_PATTERNS", {1: ["fixture"]})

    due = cron_runtime.find_missing_original_exhibition_races(
        datetime(2026, 8, 14, 11, 55, tzinfo=JST),
        target_date="2026-08-14",
        past_min=60,
        future_min=30,
        limit=10,
    )

    assert [row[0] for row in due] == ["race-2", "race-3", "race-4"]
