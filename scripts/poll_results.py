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
import sys
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD), 省略時は今日")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="最初のレース締切前でも取得する")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    target_date = (datetime.fromisoformat(args.date).date()
                   if args.date else date.today())
    now = datetime.now()

    # スキップ判定: 最初のレース締切より前なら何もしない
    if not args.force:
        with db_connect() as conn:
            cur = conn.execute("""
                SELECT MIN(race_closed_at), COUNT(*),
                       SUM(CASE WHEN race_id IN (
                         SELECT DISTINCT race_id FROM race_payouts WHERE bet_type='trifecta'
                       ) THEN 1 ELSE 0 END) AS done_count
                FROM races WHERE race_date = ?
            """, (target_date.isoformat(),))
            row = cur.fetchone()
            min_close, n_races, n_done = row if row else (None, 0, 0)

        if n_races == 0:
            print(f"[{target_date}] レースなし、スキップ")
            return
        if min_close:
            try:
                first_close = datetime.fromisoformat(str(min_close))
                # 最初のレース締切から 5 分経過していなければスキップ
                if now < first_close + timedelta(minutes=5):
                    print(f"[{target_date}] 最初のレース ({first_close}) 締切後 5分待ち中、スキップ")
                    return
            except Exception:
                pass
        if n_done >= n_races:
            print(f"[{target_date}] 全 {n_races} レース確定済、スキップ")
            return
        print(f"[{target_date}] {n_done}/{n_races} レース確定済、結果取得開始")

    # API から結果を取得 (Open API = boatraceopenapi.github.io)
    # 注: GitHub Pages 経由でバッチ更新されるため数時間遅延がある。
    payload = fetch_results(target_date)
    n_openapi = 0
    if payload:
        n_openapi = len(payload.get("results", []))
        print(f"[{target_date}] Open API: {n_openapi} レース分の結果")

    # DB に書き込み (DATABASE_URL あれば Supabase に直書き)
    with db_connect() as conn:
        try:
            if payload:
                n_results = upsert_results(conn, payload)
                conn.commit()
                print(f"  upsert_results (Open API): {n_results} 行更新")
        except Exception as e:
            print(f"  Open API ERROR: {e}")

        # === Layer 3 フォールバック: boatrace.jp から直接スクレイプ ===
        # Open API の更新は数時間遅延するため、締切から 5 分以上経過しても
        # race_payouts に乗っていないレースを補完する。
        try:
            scraped = scrape_results_for_pending_races(target_date, conn)
            n_scraped = len(scraped["results"])
            if n_scraped > 0:
                print(f"  Layer3 scrape: {n_scraped} レースを boatrace.jp から取得")
                n_added = upsert_results(conn, scraped)
                conn.commit()
                print(f"  upsert_results (Layer3): {n_added} 行更新")
            else:
                print(f"  Layer3 scrape: 補完対象なし (Open API で全て埋まっているか、まだレース直後)")
        except Exception as e:
            print(f"  Layer3 ERROR: {e}")

    print(f"[{target_date}] 完了")


if __name__ == "__main__":
    main()
