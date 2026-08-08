from __future__ import annotations

import sqlite3

from scripts import odds_scheduler


def test_existing_completed_snapshots_uses_status_table_when_available(tmp_path) -> None:
    db_path = tmp_path / "scheduler_status.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE odds_fetch_status (
          race_id TEXT NOT NULL,
          snapshot_label TEXT NOT NULL,
          state TEXT NOT NULL,
          detail_code TEXT NOT NULL,
          http_status INTEGER,
          combination_count INTEGER NOT NULL DEFAULT 0,
          retryable INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0,
          checked_at TEXT NOT NULL,
          last_success_at TEXT,
          note TEXT,
          PRIMARY KEY (race_id, snapshot_label)
        );
        CREATE TABLE odds_trifecta (
          race_id TEXT NOT NULL,
          combination TEXT NOT NULL,
          odds REAL NOT NULL,
          is_final INTEGER NOT NULL,
          recorded_at TEXT NOT NULL,
          snapshot_label TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO odds_fetch_status
            (race_id, snapshot_label, state, detail_code, combination_count, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("RACE-A", "T-5min", "fetched", "fetched", 120, "2026-08-08T10:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO odds_fetch_status
            (race_id, snapshot_label, state, detail_code, combination_count, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("RACE-B", "T-5min", "missing", "partial_data", 90, "2026-08-08T10:01:00+00:00"),
    )
    conn.commit()

    existing = odds_scheduler._existing_completed_snapshots(conn)
    conn.close()

    assert ("RACE-A", "T-5min") in existing
    assert ("RACE-B", "T-5min") not in existing


def test_existing_completed_snapshots_falls_back_to_complete_combinations(tmp_path) -> None:
    db_path = tmp_path / "scheduler_fallback.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE odds_trifecta (
          race_id TEXT NOT NULL,
          combination TEXT NOT NULL,
          odds REAL NOT NULL,
          is_final INTEGER NOT NULL,
          recorded_at TEXT NOT NULL,
          snapshot_label TEXT
        );
        """
    )
    rows = []
    for i in range(120):
        rows.append(("RACE-C", f"c{i}", float(i + 1), 0, "2026-08-08T10:00:00+00:00", "T-5min"))
    rows.append(("RACE-D", "1-2-3", 9.9, 0, "2026-08-08T10:00:00+00:00", "T-5min"))
    conn.executemany("INSERT INTO odds_trifecta VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()

    existing = odds_scheduler._existing_completed_snapshots(conn)
    conn.close()

    assert ("RACE-C", "T-5min") in existing
    assert ("RACE-D", "T-5min") not in existing
