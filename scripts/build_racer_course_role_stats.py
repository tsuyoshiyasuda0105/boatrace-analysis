from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


# --local のときは config import より先に .env の本番接続先を無効化する。
if "--local" in sys.argv:
    os.environ.pop("DATABASE_URL", None)
    os.environ["DATABASE_URL"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect


JST = ZoneInfo("Asia/Tokyo")
logger = logging.getLogger("racer-course-role-stats")


def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS racer_course_role_snapshots (
          snapshot_date         TEXT NOT NULL,
          racer_number          INTEGER NOT NULL,
          window_days           INTEGER NOT NULL,
          course1_starts        INTEGER NOT NULL,
          course1_wins          INTEGER NOT NULL,
          course1_win_rate      REAL,
          course2_starts        INTEGER NOT NULL,
          course2_nigashi_count INTEGER NOT NULL,
          course2_nigashi_rate  REAL,
          updated_at            TEXT NOT NULL,
          PRIMARY KEY (snapshot_date, racer_number)
        );
        """
    )
    conn.commit()


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


def _history_rows(
    conn,
    snapshot_date: str,
    window_start: str,
) -> Iterable[tuple[int, int, int, int]]:
    # 当日出走選手のコース1・2履歴を一度のクエリで取得する。
    return conn.execute(
        """
        WITH target AS (
          SELECT DISTINCT e.racer_number
            FROM races r
            JOIN race_entries e ON e.race_id = r.race_id
           WHERE r.race_date = ?
             AND e.racer_number IS NOT NULL
        ),
        relevant AS (
          SELECT DISTINCT r.race_id
            FROM races r
            JOIN race_entries e ON e.race_id = r.race_id
            JOIN target t ON t.racer_number = e.racer_number
           WHERE r.race_date >= ?
             AND r.race_date < ?
        ),
        win AS (
          SELECT rr.race_id,
                 MAX(
                   CASE
                     WHEN COALESCE(NULLIF(rr.course_number, 0), rr.boat_number) = 1
                     THEN 1 ELSE 0
                   END
                 ) AS course1_won
            FROM race_results rr
            JOIN relevant h ON h.race_id = rr.race_id
           WHERE rr.finishing_position = 1
           GROUP BY rr.race_id
        ),
        participant AS (
          SELECT h.race_id,
                 e.racer_number,
                 MAX(COALESCE(NULLIF(rr.course_number, 0), e.boat_number)) AS course_number,
                 MAX(CASE WHEN rr.finishing_position = 1 THEN 1 ELSE 0 END) AS racer_won,
                 MAX(CASE WHEN rr.finishing_position IS NOT NULL THEN 1 ELSE 0 END) AS finished
            FROM relevant h
            JOIN race_entries e ON e.race_id = h.race_id
            JOIN target t ON t.racer_number = e.racer_number
            JOIN race_results rr
              ON rr.race_id = e.race_id
             AND rr.boat_number = e.boat_number
           GROUP BY h.race_id, e.racer_number
        )
        SELECT p.racer_number,
               p.course_number,
               p.racer_won,
               COALESCE(w.course1_won, 0)
          FROM participant p
          LEFT JOIN win w ON w.race_id = p.race_id
         WHERE p.finished = 1
           AND p.course_number IN (1, 2)
        """,
        (snapshot_date, window_start, snapshot_date),
    ).fetchall()


def build_rows(conn, snapshot_date: str, window_days: int = 365) -> list[tuple]:
    snapshot = date.fromisoformat(snapshot_date)
    if window_days <= 0:
        raise ValueError("window_days must be greater than zero")

    racer_numbers = _target_racers(conn, snapshot_date)
    if not racer_numbers:
        return []

    window_start = (snapshot - timedelta(days=window_days)).isoformat()
    agg: dict[int, dict[str, int]] = defaultdict(
        lambda: {"course1_starts": 0, "course1_wins": 0, "course2_starts": 0, "nigashi": 0}
    )
    for racer_number, course_number, racer_won, course1_won in _history_rows(
        conn,
        snapshot_date,
        window_start,
    ):
        rec = agg[int(racer_number)]
        if int(course_number) == 1:
            rec["course1_starts"] += 1
            rec["course1_wins"] += int(racer_won)
        elif int(course_number) == 2:
            rec["course2_starts"] += 1
            rec["nigashi"] += int(course1_won)

    updated_at = datetime.now(JST).isoformat(timespec="seconds")
    rows: list[tuple] = []
    for racer_number in racer_numbers:
        rec = agg[int(racer_number)]
        course1_starts = rec["course1_starts"]
        course2_starts = rec["course2_starts"]
        rows.append(
            (
                snapshot_date,
                int(racer_number),
                window_days,
                course1_starts,
                rec["course1_wins"],
                rec["course1_wins"] / course1_starts if course1_starts else None,
                course2_starts,
                rec["nigashi"],
                rec["nigashi"] / course2_starts if course2_starts else None,
                updated_at,
            )
        )
    return rows


def upsert_rows(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    ensure_schema(conn)
    conn.executemany(
        """
        INSERT INTO racer_course_role_snapshots (
          snapshot_date,
          racer_number,
          window_days,
          course1_starts,
          course1_wins,
          course1_win_rate,
          course2_starts,
          course2_nigashi_count,
          course2_nigashi_rate,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, racer_number) DO UPDATE SET
          window_days = excluded.window_days,
          course1_starts = excluded.course1_starts,
          course1_wins = excluded.course1_wins,
          course1_win_rate = excluded.course1_win_rate,
          course2_starts = excluded.course2_starts,
          course2_nigashi_count = excluded.course2_nigashi_count,
          course2_nigashi_rate = excluded.course2_nigashi_rate,
          updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def build(snapshot_date: str, window_days: int = 365) -> tuple[int, int, int]:
    # direct=True: web の共有プールを使わない。夜間 cron の長時間集計で接続を
    # 握り続けても閲覧者を待たせないよう、バッチ専用の短命な直結接続を使う。
    with db_connect(direct=True) as conn:
        rows = build_rows(conn, snapshot_date, window_days)
        if rows:
            upsert_rows(conn, rows)
    course1_rows = sum(1 for row in rows if row[3] > 0)
    course2_rows = sum(1 for row in rows if row[6] > 0)
    return len(rows), course1_rows, course2_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat())
    parser.add_argument("--window-days", type=int, default=365)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--log-file")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    handlers: list[logging.Handler] = []
    if args.verbose:
        handlers.append(logging.StreamHandler())
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )

    started_at = time.monotonic()
    try:
        date.fromisoformat(args.date)
        if args.window_days <= 0:
            raise ValueError("--window-days must be greater than zero")
        logger.info("集計開始: date=%s window_days=%d", args.date, args.window_days)
        racers, course1_rows, course2_rows = build(args.date, args.window_days)
    except Exception as exc:
        logger.exception("集計に失敗しました: %s", exc)
        print(f"[summary] date={args.date} error={exc} elapsed={time.monotonic() - started_at:.2f}s")
        return 1

    summary = (
        f"[summary] date={args.date} racers={racers} "
        f"course1_rows={course1_rows} course2_rows={course2_rows} "
        f"elapsed={time.monotonic() - started_at:.2f}s"
    )
    logger.info(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
