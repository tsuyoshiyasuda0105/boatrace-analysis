import sqlite3

import pytest

from scripts.build_derived_start_stats import build_rows, upsert_rows
from src.start_prediction.repository import StartPredictionRepository


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT NOT NULL,
          stadium_number INTEGER NOT NULL,
          race_number INTEGER NOT NULL
        );
        CREATE TABLE race_entries (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          racer_number INTEGER NOT NULL,
          PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE race_results (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          start_timing REAL,
          PRIMARY KEY (race_id, boat_number)
        );
        """
    )
    StartPredictionRepository(conn).ensure_schema()
    return conn


def _insert_start(conn, race_id, race_date, racer_number, start_timing):
    conn.execute("INSERT INTO races VALUES (?, ?, 1, 1)", (race_id, race_date))
    conn.execute("INSERT INTO race_entries VALUES (?, 1, ?)", (race_id, racer_number))
    conn.execute("INSERT INTO race_results VALUES (?, 1, ?)", (race_id, start_timing))


def test_build_rows_uses_only_previous_race_days():
    conn = _conn()
    _insert_start(conn, "20260101-01-01", "2026-01-01", 1001, 0.11)
    _insert_start(conn, "20260110-01-01", "2026-01-10", 1001, 0.13)
    _insert_start(conn, "20260701-01-01", "2026-07-01", 1001, 0.30)
    conn.execute("INSERT INTO races VALUES ('20260701-01-02', '2026-07-01', 1, 2)")
    conn.execute("INSERT INTO race_entries VALUES ('20260701-01-02', 1, 1001)")

    rows = build_rows(conn, "2026-07-01", "2026-07-01")

    target = [row for row in rows if row[0] == "20260701-01-02"][0]
    assert target[2] == 0.13
    assert target[3] == 1
    assert target[4] == pytest.approx(0.12)
    assert target[5] == 2


def test_build_rows_excludes_starts_older_than_180_days_but_keeps_last_12():
    conn = _conn()
    _insert_start(conn, "20251201-01-01", "2025-12-01", 1001, 0.10)
    _insert_start(conn, "20260201-01-01", "2026-02-01", 1001, 0.20)
    conn.execute("INSERT INTO races VALUES ('20260701-01-01', '2026-07-01', 1, 1)")
    conn.execute("INSERT INTO race_entries VALUES ('20260701-01-01', 1, 1001)")

    rows = build_rows(conn, "2026-07-01", "2026-07-01")

    assert rows[0][2] == 0.20
    assert rows[0][3] == 1
    assert rows[0][4] == pytest.approx(0.15)
    assert rows[0][5] == 2


def test_upsert_rows_materializes_derived_start_stats():
    conn = _conn()
    rows = [
        ("20260701-01-01", 1, 0.15, 30, 0.14, 12, "2026-07-01T00:00:00"),
    ]

    assert upsert_rows(conn, rows) == 1

    got = conn.execute(
        """
        SELECT derived_avg_start_timing_180d, derived_start_count_180d,
               derived_avg_start_timing_12, derived_start_count_12
          FROM derived_start_stats
         WHERE race_id = ? AND boat_number = ?
        """,
        ("20260701-01-01", 1),
    ).fetchone()
    assert got == (0.15, 30, 0.14, 12)
