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

# 自分をバッチとして宣言する。夜間 cron から呼ばれるときは親 (render_maintenance_
# scheduler) の値を継承できるが、手動・単体で走らせると既定の statement_timeout
# 8 秒に当たって 1 年ぶんの集計が必ず QueryCanceled で落ちる (2026-09-04 に本番で
# 再現)。他のバッチ (ensure_performance_indexes.py 等) と同じく setdefault で宣言し、
# 直結接続 + タイムアウト無しを自力で確保する。
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-maintenance")

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
          -- DOUBLE PRECISION にするのは REAL が Postgres では 4 バイトになり、
          -- 42/60 や 26/40 の「ちょうど 0.70 / 0.65」を 0.6999999... に丸めて
          -- しまうため。閾値がその値ちょうどなので、境界の選手が SQL 側の
          -- 比較で静かに落ちる (2026-09-04)。判定自体は整数の分母分子から
          -- 出すので率は表示用だが、後から SQL で数えた人が別の答えを得る
          -- 状態を残さない。SQLite では REAL 相当 (8 バイト) で従来どおり。
          course1_win_rate      DOUBLE PRECISION,
          course2_starts        INTEGER NOT NULL,
          course2_nigashi_count INTEGER NOT NULL,
          course2_nigashi_rate  DOUBLE PRECISION,
          updated_at            TEXT NOT NULL,
          course1_sashinuke_count INTEGER NOT NULL DEFAULT 0,
          course1_sashinuke_rate  DOUBLE PRECISION,
          course4_starts        INTEGER NOT NULL DEFAULT 0,
          course4_makuri_wins   INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (snapshot_date, racer_number)
        );
        """
    )
    # 「差され注意」タグ (2026-09-13) 用に後から足した列。本番の既存テーブルには
    # CREATE TABLE IF NOT EXISTS では入らないので、無ければ足す。
    # 既存行は 0 / NULL になり、再集計されるまでタグは付かない (誤って付かない側)。
    ensure_column(conn, "course1_sashinuke_count", "course1_sashinuke_count INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "course1_sashinuke_rate", "course1_sashinuke_rate DOUBLE PRECISION")
    # 「4まくり注意」タグ (2026-09-13 に朝の事前計算へ移した) 用。
    ensure_column(conn, "course4_starts", "course4_starts INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "course4_makuri_wins", "course4_makuri_wins INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def ensure_column(conn, column_name: str, ddl: str) -> None:
    table_name = "racer_course_role_snapshots"
    if getattr(conn, "_kind", "sqlite") == "postgres":
        columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = ?
                """,
                (table_name,),
            )
        }
    else:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


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
) -> Iterable[tuple[int, int, int, int, int]]:
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
                 ) AS course1_won,
                 -- 勝者の決まり手が「差し」。1コース艇が負けた回だけ数える (差し負け)。
                 MAX(CASE WHEN rr.kimarite = '差し' THEN 1 ELSE 0 END) AS sashi_won
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
               COALESCE(w.course1_won, 0),
               COALESCE(w.sashi_won, 0)
          FROM participant p
          LEFT JOIN win w ON w.race_id = p.race_id
         WHERE p.finished = 1
           AND p.course_number IN (1, 2)
        """,
        (snapshot_date, window_start, snapshot_date),
    ).fetchall()


def _course4_rows(
    conn,
    snapshot_date: str,
    window_start: str,
) -> Iterable[tuple[int, int, int]]:
    # 4まくり注意タグ用。以前レース詳細タグの生成時にその場で数えていた定義を
    # そのまま移した: 4コース (course_number=4) の出走数と、そのうち
    # 1着かつ決まり手「まくり」の数。窓は MAKURI_WINDOW_DAYS。
    return conn.execute(
        """
        WITH target AS (
          SELECT DISTINCT e.racer_number
            FROM races r
            JOIN race_entries e ON e.race_id = r.race_id
           WHERE r.race_date = ?
             AND e.racer_number IS NOT NULL
        )
        SELECT e.racer_number,
               COUNT(*) AS starts,
               SUM(CASE WHEN rr.finishing_position = 1 AND rr.kimarite = 'まくり' THEN 1 ELSE 0 END) AS makuri_wins
          FROM target t
          JOIN race_entries e ON e.racer_number = t.racer_number
          JOIN races r
            ON r.race_id = e.race_id
           AND r.race_date >= ?
           AND r.race_date < ?
          JOIN race_results rr
            ON rr.race_id = e.race_id
           AND rr.boat_number = e.boat_number
           AND rr.course_number = 4
         GROUP BY e.racer_number
        """,
        (snapshot_date, window_start, snapshot_date),
    ).fetchall()


# 4まくり注意の集計窓。以前の「前日までの全期間」と、本番の現データ (2024年〜)
# では同じ結果になる 3 年に固定し、日がたっても読む量が増えないようにした
# (2026-09-13 本番で 9/13 の対象 16 レースが全期間版と完全一致・約1.5秒)。
MAKURI_WINDOW_DAYS = 1095


def build_rows(conn, snapshot_date: str, window_days: int = 365) -> list[tuple]:
    snapshot = date.fromisoformat(snapshot_date)
    if window_days <= 0:
        raise ValueError("window_days must be greater than zero")

    racer_numbers = _target_racers(conn, snapshot_date)
    if not racer_numbers:
        return []

    window_start = (snapshot - timedelta(days=window_days)).isoformat()
    agg: dict[int, dict[str, int]] = defaultdict(
        lambda: {
            "course1_starts": 0,
            "course1_wins": 0,
            "sashinuke": 0,
            "course2_starts": 0,
            "nigashi": 0,
        }
    )
    for racer_number, course_number, racer_won, course1_won, sashi_won in _history_rows(
        conn,
        snapshot_date,
        window_start,
    ):
        rec = agg[int(racer_number)]
        if int(course_number) == 1:
            rec["course1_starts"] += 1
            rec["course1_wins"] += int(racer_won)
            if not int(racer_won) and int(sashi_won):
                rec["sashinuke"] += 1
        elif int(course_number) == 2:
            rec["course2_starts"] += 1
            rec["nigashi"] += int(course1_won)

    makuri_window_start = (snapshot - timedelta(days=MAKURI_WINDOW_DAYS)).isoformat()
    course4 = {
        int(racer_number): (int(starts or 0), int(makuri_wins or 0))
        for racer_number, starts, makuri_wins in _course4_rows(conn, snapshot_date, makuri_window_start)
    }

    updated_at = datetime.now(JST).isoformat(timespec="seconds")
    rows: list[tuple] = []
    for racer_number in racer_numbers:
        rec = agg[int(racer_number)]
        course4_starts, course4_makuri_wins = course4.get(int(racer_number), (0, 0))
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
                rec["sashinuke"],
                rec["sashinuke"] / course1_starts if course1_starts else None,
                course4_starts,
                course4_makuri_wins,
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
          updated_at,
          course1_sashinuke_count,
          course1_sashinuke_rate,
          course4_starts,
          course4_makuri_wins
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, racer_number) DO UPDATE SET
          window_days = excluded.window_days,
          course1_starts = excluded.course1_starts,
          course1_wins = excluded.course1_wins,
          course1_win_rate = excluded.course1_win_rate,
          course2_starts = excluded.course2_starts,
          course2_nigashi_count = excluded.course2_nigashi_count,
          course2_nigashi_rate = excluded.course2_nigashi_rate,
          updated_at = excluded.updated_at,
          course1_sashinuke_count = excluded.course1_sashinuke_count,
          course1_sashinuke_rate = excluded.course1_sashinuke_rate,
          course4_starts = excluded.course4_starts,
          course4_makuri_wins = excluded.course4_makuri_wins
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
