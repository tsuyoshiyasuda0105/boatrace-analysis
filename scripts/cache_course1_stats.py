"""course1_stats_cache の事前集計バッチ.

L4+1c80 ランク判定に使う「過去180日 × boat=1 出走 × 1着率」を
事前に計算して course1_stats_cache テーブルに保存する.

呼び出し側 (src/web/app.py の market-signals endpoint) は
cache テーブルがあれば SELECT 1 回で取得、 無ければ旧 SQL に fallback.

設計:
  - 1 選手につき 1 row × 1 as_of_date
  - PK = (racer_number, as_of_date)
  - 最新の as_of_date のみ常に保持 (古いものは定期的に削除して OK)
  - 1 日 1 回バッチ実行 (深夜 / daily_collect の直後 推奨)

実行方法:
  py -3 scripts/cache_course1_stats.py              # 今日分を集計
  py -3 scripts/cache_course1_stats.py --date 2026-05-29
  py -3 scripts/cache_course1_stats.py --keep-days 30  # 古いキャッシュも 30日分保持
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import config

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

WINDOW_DAYS = 180  # COURSE1_WINDOW_DAYS (l4_strategy と整合)


def ensure_schema(conn) -> None:
    """course1_stats_cache テーブルと index を作成 (存在しない場合)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS course1_stats_cache (
            racer_number INTEGER NOT NULL,
            as_of_date TEXT NOT NULL,
            starts INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            PRIMARY KEY (racer_number, as_of_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_course1_cache_date
        ON course1_stats_cache(as_of_date)
    """)
    conn.commit()


def populate(conn, as_of: date) -> int:
    """as_of を「集計時点」として past 180 日のデータで集計を更新.

    Returns: INSERT/REPLACE された行数
    """
    cutoff = (as_of - timedelta(days=WINDOW_DAYS)).isoformat()
    as_of_iso = as_of.isoformat()

    # 既存値を上書き (REPLACE)
    cur = conn.execute("""
        INSERT OR REPLACE INTO course1_stats_cache
            (racer_number, as_of_date, starts, wins, win_rate)
        SELECT
            e.racer_number,
            ? AS as_of_date,
            COUNT(res.race_id) AS starts,
            SUM(CASE WHEN res.finishing_position = 1 THEN 1 ELSE 0 END) AS wins,
            1.0 * SUM(CASE WHEN res.finishing_position = 1 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(res.race_id), 0) AS win_rate
        FROM race_entries e
        JOIN races r ON r.race_id = e.race_id
        LEFT JOIN race_results res ON res.race_id = e.race_id AND res.boat_number = 1
        WHERE e.boat_number = 1
          AND r.race_date < ?
          AND r.race_date >= ?
          AND res.finishing_position IS NOT NULL
        GROUP BY e.racer_number
        HAVING starts >= 1
    """, (as_of_iso, as_of_iso, cutoff))
    n = cur.rowcount
    conn.commit()
    return n


def cleanup_old(conn, keep_days: int) -> int:
    """as_of_date が `keep_days` 日より古い行を削除.

    既定の使い方では当日分しか必要ないので keep_days=7 程度で十分.
    """
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    cur = conn.execute(
        "DELETE FROM course1_stats_cache WHERE as_of_date < ?",
        (cutoff,),
    )
    n = cur.rowcount
    conn.commit()
    return n


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, default=None,
                   help="集計時点 (YYYY-MM-DD, 省略時は今日)")
    p.add_argument("--keep-days", type=int, default=7,
                   help="古い cache を何日分残すか (default 7日)")
    args = p.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    logger.info("course1_stats_cache 集計開始 as_of=%s window=%d days",
                target.isoformat(), WINDOW_DAYS)

    db_path = config.DB_PATH
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        n_inserted = populate(conn, target)
        logger.info("集計完了: %d 選手分", n_inserted)
        n_deleted = cleanup_old(conn, args.keep_days)
        if n_deleted:
            logger.info("古い cache 削除: %d 行", n_deleted)
    finally:
        conn.close()
    print(f"OK: as_of={target.isoformat()} inserted={n_inserted}")


if __name__ == "__main__":
    main()
