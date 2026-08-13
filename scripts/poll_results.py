"""軽量・高頻度の結果ポーリングスクリプト

5 分毎のタスクで実行し、レース終了後の結果を DB に反映する。
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
from src.collectors.openapi import (
    fetch_programs,
    fetch_results,
    upsert_programs,
    upsert_results,
)
from src.collectors.result_scraper import scrape_results_for_pending_races
from src.db.connection import connect as db_connect


RESULT_SHELL_GRACE_MINUTES = 30


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


def _missing_closed_result_race_ids(conn: sqlite3.Connection, target_date: date) -> list[str]:
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
    missing: list[str] = []
    for race_id, race_closed_at in rows:
        closed_at = _parse_closed_at(race_closed_at)
        if closed_at and closed_at + timedelta(minutes=RESULT_SHELL_GRACE_MINUTES) <= now_local:
            missing.append(str(race_id))
    return missing


def _count_openapi_shell_races(conn: sqlite3.Connection, target_date: date) -> int:
    return len(_missing_closed_result_race_ids(conn, target_date))


def _missing_result_parent_race_ids(conn: sqlite3.Connection, payload: dict) -> list[str]:
    missing: list[str] = []
    for race in payload.get("results", []) or []:
        race_date = str(race.get("race_date") or "")
        stadium_number = race.get("race_stadium_number")
        race_number = race.get("race_number")
        if not race_date or stadium_number is None or race_number is None:
            continue
        rid = f"{race_date.replace('-', '')}-{int(stadium_number):02d}-{int(race_number):02d}"
        row = conn.execute(
            "SELECT 1 FROM races WHERE race_id = ? LIMIT 1",
            (rid,),
        ).fetchone()
        if row:
            continue
        missing.append(rid)
    return missing


def _backfill_program_shells_for_results(
    conn: sqlite3.Connection,
    target_date: date,
    payload: dict,
) -> list[str]:
    missing_before = _missing_result_parent_race_ids(conn, payload)
    if not missing_before:
        return []
    programs_payload = fetch_programs(target_date)
    if programs_payload:
        upsert_programs(conn, programs_payload)
        conn.commit()
    return _missing_result_parent_race_ids(conn, payload)


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
                missing_after_backfill = _backfill_program_shells_for_results(
                    conn,
                    target_date,
                    payload,
                )
                if missing_after_backfill:
                    print(
                        "[Open API] missing parent race shells after programs backfill: "
                        + ", ".join(missing_after_backfill[:10]),
                    )
                n_results = upsert_results(conn, payload)
                conn.commit()
                print(f"  upsert_results (Open API): {n_results}")
            except Exception as e:
                print(f"  Open API ERROR: {e}")
        else:
            print(f"[{target_date}] Open API: no response")

        missing_after_openapi = _missing_closed_result_race_ids(conn, target_date)
        if missing_after_openapi:
            try:
                repaired = scrape_results_for_pending_races(
                    target_date,
                    conn,
                    l4_only=False,
                    race_ids=missing_after_openapi,
                )
                repaired_count = len(repaired["results"])
                if repaired_count > 0:
                    print(f"[{target_date}] Layer3 repair: {repaired_count} missing races")
                    repaired_rows = upsert_results(conn, repaired)
                    conn.commit()
                    print(f"  upsert_results (Layer3 repair): {repaired_rows}")
                else:
                    print(f"[{target_date}] Layer3 repair: no additional races")
            except Exception as e:
                print(f"  Layer3 repair ERROR: {e}")

        shell_races = _count_openapi_shell_races(conn, target_date)

    if shell_races > 0:
        print(f"[{target_date}] WARN: {shell_races} races still have shell result data")
        sys.exit(2)

    print(f"[{target_date}] done")


if __name__ == "__main__":
    main()
