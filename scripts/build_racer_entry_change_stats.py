from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect


ENTRY_CHANGE_MIN_STARTS = 100
ENTRY_CHANGE_WATCH_RATE = 0.15
ENTRY_CHANGE_HIGH_RATE = 0.20


def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS racer_entry_change_snapshots (
          snapshot_date       TEXT NOT NULL,
          racer_number        INTEGER NOT NULL,
          starts_count        INTEGER NOT NULL,
          change_count        INTEGER NOT NULL,
          change_rate         REAL NOT NULL,
          inner_change_count  INTEGER NOT NULL,
          inner_change_rate   REAL NOT NULL,
          outer_change_count  INTEGER NOT NULL,
          outer_change_rate   REAL NOT NULL,
          level               TEXT,
          updated_at          TEXT NOT NULL,
          PRIMARY KEY (snapshot_date, racer_number)
        );
        CREATE INDEX IF NOT EXISTS idx_racer_entry_change_snapshot_date
          ON racer_entry_change_snapshots(snapshot_date, level, change_rate);
        """
    )
    conn.commit()


def classify_level(starts_count: int, change_rate: float) -> str | None:
    if starts_count < ENTRY_CHANGE_MIN_STARTS:
        return None
    if change_rate >= ENTRY_CHANGE_HIGH_RATE:
        return "high"
    if change_rate >= ENTRY_CHANGE_WATCH_RATE:
        return "watch"
    return None


def _target_racers(conn, snapshot_date: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT e.racer_number
          FROM races r
          JOIN race_entries e ON e.race_id = r.race_id
         WHERE r.race_date = ?
           AND e.racer_number IS NOT NULL
         ORDER BY e.racer_number
        """,
        (snapshot_date,),
    ).fetchall()
    return [int(row[0]) for row in rows if row and row[0] is not None]


def _history_rows(conn, snapshot_date: str, racer_numbers: list[int]) -> Iterable[tuple[int, int, int]]:
    if not racer_numbers:
        return []
    placeholders = ",".join("?" for _ in racer_numbers)
    sql = f"""
        SELECT e.racer_number,
               p.boat_number,
               p.course_number
          FROM race_previews p
          JOIN race_entries e
            ON e.race_id = p.race_id
           AND e.boat_number = p.boat_number
          JOIN races r
            ON r.race_id = p.race_id
         WHERE r.race_date < ?
           AND e.racer_number IN ({placeholders})
           AND p.course_number BETWEEN 1 AND 6
         ORDER BY e.racer_number, r.race_date, p.race_id, p.boat_number
    """
    return conn.execute(sql, (snapshot_date, *racer_numbers)).fetchall()


def build_rows(conn, snapshot_date: str) -> list[tuple]:
    racer_numbers = _target_racers(conn, snapshot_date)
    if not racer_numbers:
        return []

    agg: dict[int, dict[str, int]] = defaultdict(
        lambda: {"starts": 0, "change": 0, "inner": 0, "outer": 0}
    )
    for racer_number, boat_number, course_number in _history_rows(conn, snapshot_date, racer_numbers):
        racer = int(racer_number)
        boat = int(boat_number)
        course = int(course_number)
        rec = agg[racer]
        rec["starts"] += 1
        if course != boat:
            rec["change"] += 1
        if course < boat:
            rec["inner"] += 1
        elif course > boat:
            rec["outer"] += 1

    updated_at = datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []
    for racer_number in racer_numbers:
        rec = agg[int(racer_number)]
        starts = int(rec["starts"])
        change = int(rec["change"])
        inner = int(rec["inner"])
        outer = int(rec["outer"])
        change_rate = (change / starts) if starts else 0.0
        inner_rate = (inner / starts) if starts else 0.0
        outer_rate = (outer / starts) if starts else 0.0
        rows.append(
            (
                snapshot_date,
                int(racer_number),
                starts,
                change,
                change_rate,
                inner,
                inner_rate,
                outer,
                outer_rate,
                classify_level(starts, change_rate),
                updated_at,
            )
        )
    return rows


def upsert_rows(conn, rows: list[tuple], snapshot_date: str) -> int:
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM racer_entry_change_snapshots WHERE snapshot_date = ?",
        (snapshot_date,),
    )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO racer_entry_change_snapshots (
              snapshot_date,
              racer_number,
              starts_count,
              change_count,
              change_rate,
              inner_change_count,
              inner_change_rate,
              outer_change_count,
              outer_change_rate,
              level,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def build(snapshot_date: str) -> tuple[int, int]:
    with db_connect() as conn:
        rows = build_rows(conn, snapshot_date)
        written = upsert_rows(conn, rows, snapshot_date)
    return len(rows), written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, written = build(args.date)
    print(
        f"[entry-change-snapshot] date={args.date} rows={rows} written={written}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
