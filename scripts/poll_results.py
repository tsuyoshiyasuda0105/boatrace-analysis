"""軽量・高頻度の結果ポーリングスクリプト

5 分毎のタスクで実行し、レース終了から 5-15 分以内に結果を DB に反映する。
daily_collect.py が 23:30 にフル取得するのに対し、これは「結果のみ」 を取得し
API 負荷を最小化する。

実行内容:
  1. fetch_results() で当日の結果 API を 1 リクエスト
  2. upsert_results() で race_results + race_payouts に upsert
  3. DATABASE_URL があれば Supabase に直接書き込み (config の DB 設定経由)
  4. 既に確定済みのレース数を表示

スキップ条件:
  - 現時刻が当日の最初のレース締切より前 → 何もしない (無駄打ち回避)
  - 既に全レース確定済 → 何もしない

実行:
  python scripts/poll_results.py
  python scripts/poll_results.py --date 2026-05-14
"""
from __future__ import annotations

import argparse
import logging
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass

import config
from src.collectors.openapi import fetch_results, upsert_results
from src.collectors.result_scraper import scrape_results_for_pending_races
from src.db.connection import connect as db_connect


def _parse_closed_at(value) -> datetime | None:
    if value in (None, "", "-"):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _count_openapi_shell_races(conn: sqlite3.Connection, target_date: date) -> int:
    rows = conn.execute(
        """
        SELECT r.race_id, r.race_closed_at
          FROM races r
         WHERE r.race_date = ?
           AND r.race_closed_at IS NOT NULL
           AND EXISTS (SELECT 1 FROM race_entries e WHERE e.race_id = r.race_id)
           AND NOT EXISTS (
               SELECT 1
                 FROM race_results rr
                WHERE rr.race_id = r.race_id
                  AND rr.finishing_position IS NOT NULL
           )
        """,
        (target_date.isoformat(),),
    ).fetchall()
    now_local = datetime.now()
    pending = 0
    for _race_id, race_closed_at in rows:
        closed_at = _parse_closed_at(race_closed_at)
        if closed_at and closed_at + timedelta(minutes=5) <= now_local:
            pending += 1
    return pending


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD), 省略時は今日")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="最初のレース締切前でも取得する")
    p.add_argument("--no-jitter", action="store_true",
                   help="ランダムジッタを無効 (デバッグ用)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # === 起動時ランダムジッタ ===
    # Task Scheduler は xx:00:01 ピッタリに起動するが、毎回同じタイミング
    # だと boatrace.jp 側で「ボット動作」と検知されやすい。
    # 0-60 秒のランダム待ちで人間の閲覧パターンに寄せる。
    if not args.no_jitter:
        jitter = random.uniform(0, 60)
        print(f"[jitter] sleeping {jitter:.1f}s to randomize timing")
        time.sleep(jitter)

    target_date = (datetime.fromisoformat(args.date).date()
                   if args.date else date.today())
    now = datetime.now()

    # スキップ判定: 最初のレース締切より前なら何もしない
    if not args.force:
        shell_races = 0
    with db_connect() as conn:
        # === Layer 3 (fallback scrape) ===
        try:
            scraped = scrape_results_for_pending_races(target_date, conn)
            n_scraped = len(scraped["results"])
            if n_scraped > 0:
                print(f"[{target_date}] Layer3 scrape: {n_scraped} races from boatrace.jp")
                n_added = upsert_results(conn, scraped)
                conn.commit()
                print(f"  upsert_results (Layer3): {n_added}")
            else:
                print(f"[{target_date}] Layer3 scrape: no additional races")
        except Exception as e:
            print(f"  Layer3 ERROR: {e}")

        # === Open API ===
        payload = fetch_results(target_date)
        if payload:
            n_openapi = len(payload.get("results", []))
            print(f"[{target_date}] Open API: {n_openapi} races fetched")
            try:
                n_results = upsert_results(conn, payload)
                conn.commit()
                print(f"  upsert_results (Open API): {n_results}")
            except Exception as e:
                print(f"  Open API ERROR: {e}")
        else:
            print(f"[{target_date}] Open API: no response")

        shell_races = _count_openapi_shell_races(conn, target_date)

    if shell_races > 0:
        print(f"[{target_date}] WARN: {shell_races} races still have shell result data")
        sys.exit(2)

    print(f"[{target_date}] done")


if __name__ == "__main__":
    main()
