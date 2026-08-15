"""Synchronize searchable trifecta odds without changing as-of features."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3
from typing import Any


ODDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshot (
  race_id TEXT NOT NULL,
  combination TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  odds REAL NOT NULL,
  PRIMARY KEY (race_id, combination, snapshot)
);
"""


def _iso_date(value: str, label: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def sync_odds(
    source: sqlite3.Connection,
    output_path: str | Path,
    date_from: str,
    date_to: str,
    *,
    rebuild: bool = False,
) -> dict[str, Any]:
    """Copy the latest T-5min/final row per ticket for a bounded date range."""

    start = _iso_date(date_from, "date_from")
    end = _iso_date(date_to, "date_to")
    if start > end:
        raise ValueError("date_from must not be after date_to")
    compact_start = start.replace("-", "")
    compact_end_exclusive = (date.fromisoformat(end) + timedelta(days=1)).isoformat().replace("-", "")
    rows = source.execute(
        """
        SELECT odds.race_id, odds.combination, odds.snapshot_label, odds.odds
        FROM odds_trifecta AS odds
        JOIN (
          SELECT race_id, combination, snapshot_label, MAX(recorded_at) AS recorded_at
          FROM odds_trifecta
          WHERE snapshot_label IN ('T-5min', 'final')
            AND race_id >= ? AND race_id < ?
          GROUP BY race_id, combination, snapshot_label
        ) AS latest
          ON latest.race_id = odds.race_id
         AND latest.combination = odds.combination
         AND latest.snapshot_label = odds.snapshot_label
         AND latest.recorded_at = odds.recorded_at
        ORDER BY odds.race_id, odds.combination, odds.snapshot_label
        """,
        (compact_start, compact_end_exclusive),
    ).fetchall()

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as destination:
        destination.executescript(ODDS_SCHEMA)
        removed = 0
        if rebuild:
            cursor = destination.execute(
                "DELETE FROM odds_snapshot WHERE race_id >= ? AND race_id < ?",
                (compact_start, compact_end_exclusive),
            )
            removed = max(0, cursor.rowcount)
        before = destination.total_changes
        destination.executemany(
            "INSERT OR IGNORE INTO odds_snapshot "
            "(race_id, combination, snapshot, odds) VALUES (?, ?, ?, ?)",
            rows,
        )
        inserted = destination.total_changes - before
    return {
        "date_from": start,
        "date_to": end,
        "source_rows": len(rows),
        "inserted": inserted,
        "skipped": len(rows) - inserted,
        "removed": removed,
    }


__all__ = ["ODDS_SCHEMA", "sync_odds"]
