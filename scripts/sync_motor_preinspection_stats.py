"""Normalize venue-specific pre-inspection rows into one feature table.

This script is intentionally local-safe by default. Historical venue pages are
collected into source-specific tables first, then copied into
motor_preinspection_stats so prediction features can read one stable table.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.getenv("ALLOW_PRODUCTION_DB", "").strip() != "1":
    os.environ["DATABASE_URL"] = ""

from src.db.connection import connect as db_connect


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    return ap.parse_args()


def has_table(conn, table_name: str) -> bool:
    if getattr(conn, "_kind", "") == "postgres":
        row = conn.execute("SELECT to_regclass(?)", (f"public.{table_name}",)).fetchone()
        return bool(row and row[0])
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return bool(row)


def ensure_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS motor_preinspection_stats (
          stadium_number INTEGER NOT NULL,
          race_date TEXT NOT NULL,
          source_name TEXT NOT NULL,
          racer_number INTEGER,
          racer_name TEXT,
          racer_class TEXT,
          motor_number INTEGER NOT NULL,
          motor_win2_rate REAL,
          boat_number INTEGER,
          boat_win2_rate REAL,
          preinspection_time REAL,
          preinspection_rank INTEGER,
          raw_text TEXT,
          source_url TEXT,
          collected_at TEXT,
          PRIMARY KEY (stadium_number, race_date, source_name, motor_number, racer_number)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_motor_preinspection_lookup
          ON motor_preinspection_stats(stadium_number, motor_number, race_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_motor_preinspection_racer
          ON motor_preinspection_stats(stadium_number, race_date, racer_number)
        """
    )
    conn.commit()


def date_filter(alias: str, start: str | None, end: str | None) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if start:
        clauses.append(f"{alias}.race_date >= ?")
        params.append(start)
    if end:
        clauses.append(f"{alias}.race_date <= ?")
        params.append(end)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def sync_ashiya(conn, start: str | None, end: str | None) -> int:
    if not has_table(conn, "ashiya_timerank"):
        return 0
    where, params = date_filter("a", start, end)
    rows = conn.execute(
        f"""
        SELECT 21 AS stadium_number, a.race_date, 'ashiya_timerank:' || a.kind AS source_name,
               a.racer_number, a.racer_name, a.racer_class, a.motor_number,
               a.motor_win2_rate, a.boat_number, a.boat_win2_rate,
               a.preinspection_time, a.rank AS preinspection_rank,
               a.raw_text, a.source_url, a.collected_at
          FROM ashiya_timerank a
          {where}
        """,
        tuple(params),
    ).fetchall()
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO motor_preinspection_stats (
          stadium_number, race_date, source_name, racer_number, racer_name,
          racer_class, motor_number, motor_win2_rate, boat_number, boat_win2_rate,
          preinspection_time, preinspection_rank, raw_text, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def sync_edogawa(conn, start: str | None, end: str | None) -> int:
    if not has_table(conn, "edogawa_motor_cyusen"):
        return 0
    where, params = date_filter("e", start, end)
    rows = conn.execute(
        f"""
        SELECT 3 AS stadium_number, e.race_date, 'edogawa_motor_cyusen' AS source_name,
               e.racer_number, e.racer_name, NULL AS racer_class, e.motor_number,
               e.motor_win2_rate, e.boat_number, e.boat_win2_rate,
               e.preinspection_time, e.rank AS preinspection_rank,
               e.raw_text, e.source_url, e.collected_at
          FROM edogawa_motor_cyusen e
          {where}
        """,
        tuple(params),
    ).fetchall()
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO motor_preinspection_stats (
          stadium_number, race_date, source_name, racer_number, racer_name,
          racer_class, motor_number, motor_win2_rate, boat_number, boat_win2_rate,
          preinspection_time, preinspection_rank, raw_text, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def main() -> int:
    args = parse_args()
    with db_connect() as conn:
        ensure_schema(conn)
        ashiya_rows = sync_ashiya(conn, args.start, args.end)
        edogawa_rows = sync_edogawa(conn, args.start, args.end)
    print(f"synced ashiya={ashiya_rows} edogawa={edogawa_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
