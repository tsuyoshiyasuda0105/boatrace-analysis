"""Cache accident-rate ranking rows for fast member-page display.

The web page should not rebuild accident rankings on every request. This
script materializes the current period ranking once during nightly jobs.

Usage:
  python scripts/cache_racer_accident_rank_snapshot.py
  python scripts/cache_racer_accident_rank_snapshot.py --date 2026-07-19
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.rebuild_racer_accident_stats import RULE_VERSION  # noqa: E402
from src.db.connection import assert_safe_production_write, connect as db_connect  # noqa: E402


JST = ZoneInfo("Asia/Tokyo")


def _today_jst_iso() -> str:
    return datetime.now(JST).date().isoformat()


def accident_period_start_for_date(date_iso: str) -> str:
    y, m, _d = [int(x) for x in str(date_iso).split("-")]
    if 5 <= m <= 10:
        return f"{y}-05-01"
    if m >= 11:
        return f"{y}-11-01"
    return f"{y - 1}-11-01"


def class_label(class_number: Any) -> str:
    try:
        cls = int(class_number)
    except (TypeError, ValueError):
        return "-"
    return {1: "A1", 2: "A2", 3: "B1", 4: "B2"}.get(cls, "-")


def accident_rank_tone(class_number: Any, accident_rate: Any) -> str:
    try:
        rate = float(accident_rate or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    try:
        cls = int(class_number)
    except (TypeError, ValueError):
        cls = None
    if rate < 0.7:
        return "normal"
    if cls in (1, 2):
        return "a"
    if cls == 3:
        return "b1"
    if cls == 4:
        return "b2"
    return "unknown"


def ensure_table(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_rank_snapshots (
          period_start       TEXT NOT NULL,
          period_end         TEXT NOT NULL,
          racer_number       INTEGER NOT NULL,
          racer_name         TEXT,
          class_number       INTEGER,
          class_label        TEXT,
          accident_points    INTEGER NOT NULL DEFAULT 0,
          accident_rate      REAL,
          starts_count       INTEGER NOT NULL DEFAULT 0,
          accident_events    INTEGER NOT NULL DEFAULT 0,
          tone               TEXT NOT NULL DEFAULT 'unknown',
          rank_no            INTEGER NOT NULL,
          source_rule_version TEXT NOT NULL,
          source_kind        TEXT NOT NULL DEFAULT 'reconstructed',
          snapshot_date      TEXT NOT NULL,
          updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (period_start, racer_number)
        );
        CREATE INDEX IF NOT EXISTS idx_racer_accident_rank_snap_period_rank
          ON racer_accident_rank_snapshots(period_start, rank_no);
        CREATE INDEX IF NOT EXISTS idx_racer_accident_rank_snap_period_class
          ON racer_accident_rank_snapshots(period_start, class_label, accident_rate);
        """
    )


def build_snapshot(target_date: str, period_start: str | None = None, db_path: str | None = None) -> dict[str, Any]:
    period_start = period_start or accident_period_start_for_date(target_date)
    with db_connect(db_path) as conn:
        ensure_table(conn)
        source_kind = "reconstructed"
        period_row = conn.execute(
            """
            SELECT source_kind, period_start, MAX(period_end) AS period_end, COUNT(*) AS n,
                   MAX(updated_at) AS source_updated_at
              FROM racer_accident_period_stats
             WHERE period_start = ?
               AND source_kind IN ('official_external', 'reconstructed')
               AND rule_version = ?
             GROUP BY source_kind, period_start
             ORDER BY CASE WHEN source_kind = 'official_external' THEN 0 ELSE 1 END,
                      MAX(period_end) DESC
             LIMIT 1
            """,
            (period_start, RULE_VERSION),
        ).fetchone()
        if not period_row:
            period_row = conn.execute(
                """
                SELECT source_kind, period_start, MAX(period_end) AS period_end, COUNT(*) AS n,
                       MAX(updated_at) AS source_updated_at
                  FROM racer_accident_period_stats
                 WHERE source_kind IN ('official_external', 'reconstructed')
                   AND rule_version = ?
                 GROUP BY source_kind, period_start
                 ORDER BY period_start DESC,
                          CASE WHEN source_kind = 'official_external' THEN 0 ELSE 1 END,
                          MAX(period_end) DESC
                 LIMIT 1
                """,
                (RULE_VERSION,),
            ).fetchone()
        if not period_row:
            raise RuntimeError("racer_accident_period_stats has no official_external/reconstructed rows")

        source_kind = str(period_row[0] or "reconstructed")
        period_start = str(period_row[1])
        period_end = str(period_row[2])
        if period_end < str(target_date):
            raise RuntimeError(
                "racer_accident_period_stats is stale "
                f"period_end={period_end} target_date={target_date}"
            )
        class_as_of = period_end
        rows = conn.execute(
            """
            WITH latest_entry AS (
                SELECT e.racer_number, e.racer_name, e.class_number,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.racer_number
                           ORDER BY r.race_date DESC, e.race_id DESC
                       ) AS rn
                  FROM race_entries e
                  JOIN races r ON r.race_id = e.race_id
                 WHERE r.race_date <= ?
            )
            SELECT s.racer_number,
                   COALESCE(NULLIF(le.racer_name, ''), rc.name, '') AS racer_name,
                   le.class_number,
                   s.accident_points,
                   s.accident_rate,
                   s.starts_count,
                   s.accident_events
              FROM racer_accident_period_stats s
              LEFT JOIN racers rc ON rc.racer_number = s.racer_number
              LEFT JOIN latest_entry le
                ON le.racer_number = s.racer_number AND le.rn = 1
             WHERE s.period_start = ?
               AND s.period_end = ?
               AND s.source_kind = ?
               AND s.rule_version = ?
              ORDER BY s.accident_rate DESC, s.accident_points DESC, s.starts_count DESC
            """,
            (class_as_of, period_start, period_end, source_kind, RULE_VERSION),
        ).fetchall()

        conn.execute(
            "DELETE FROM racer_accident_rank_snapshots WHERE period_start = ? AND source_kind = ?",
            (period_start, source_kind),
        )
        payload = []
        for rank_no, row in enumerate(rows, start=1):
            cls = row[2]
            rate = float(row[4] or 0.0)
            payload.append(
                (
                    period_start,
                    period_end,
                    int(row[0]),
                    row[1] or "",
                    int(cls) if cls is not None else None,
                    class_label(cls),
                    int(row[3] or 0),
                    rate,
                    int(row[5] or 0),
                    int(row[6] or 0),
                    accident_rank_tone(cls, rate),
                    rank_no,
                    RULE_VERSION,
                    source_kind,
                    target_date,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO racer_accident_rank_snapshots
              (period_start, period_end, racer_number, racer_name, class_number,
               class_label, accident_points, accident_rate, starts_count,
               accident_events, tone, rank_no, source_rule_version, source_kind,
               snapshot_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
    return {
        "period_start": period_start,
        "period_end": period_end,
        "rows": len(payload),
        "snapshot_date": target_date,
        "source_kind": source_kind,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_today_jst_iso())
    ap.add_argument("--period")
    ap.add_argument("--db-path", help="Use a local SQLite DB path even when DATABASE_URL exists.")
    args = ap.parse_args()
    assert_safe_production_write(
        action="cache_racer_accident_rank_snapshot",
        db_path=args.db_path,
        allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
    )
    summary = build_snapshot(args.date, args.period, args.db_path)
    print(
        "cached accident ranking "
        f"period={summary['period_start']}..{summary['period_end']} "
        f"rows={summary['rows']} snapshot_date={summary['snapshot_date']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
