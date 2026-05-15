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

    # DB に書き込み (DATABASE_URL あれば Supabase に直書き)
    #
    # 順序: Layer 3 (速報) → Open API (上書き=正)
    # 1. Layer 3 boatrace.jp スクレイプ
    #    レース終了 ~5 分後にすぐ反映。L4 [A1] 候補のみ (BAN リスク低減)。
    # 2. Open API (公式バッチ、数時間遅延)
    #    全レース対象。Layer 3 が書いた値も同 race_id で上書きする
    #    (Open API の数値が「正」)。
    # → ROI 速報性を保ちつつ、最終的に Open API データに収束する。
    with db_connect() as conn:
        # === Layer 3 (速報): L4 [A1] 候補のみ ===
        try:
            scraped = scrape_results_for_pending_races(target_date, conn)
            n_scraped = len(scraped["results"])
            if n_scraped > 0:
                print(f"[{target_date}] Layer3 scrape: {n_scraped} レースを boatrace.jp から取得 (速報)")
                n_added = upsert_results(conn, scraped)
                conn.commit()
                print(f"  upsert_results (Layer3 速報): {n_added} 行")
            else:
                print(f"[{target_date}] Layer3 scrape: 補完対象なし")
        except Exception as e:
            print(f"  Layer3 ERROR: {e}")

        # === Open API (公式バッチ): 全レース対象、上書き正 ===
        payload = fetch_results(target_date)
        if payload:
            n_openapi = len(payload.get("results", []))
            print(f"[{target_date}] Open API: {n_openapi} レース分の結果")
            try:
                n_results = upsert_results(conn, payload)
                conn.commit()
                print(f"  upsert_results (Open API 上書き): {n_results} 行")
            except Exception as e:
                print(f"  Open API ERROR: {e}")
        else:
            print(f"[{target_date}] Open API: レスポンスなし")

    print(f"[{target_date}] 完了")


if __name__ == "__main__":
    main()
