"""
Layer 1 公式DLバックフィル

usage:
    python scripts/backfill_official.py --start 2022-05-08 --end 2025-05-07
    python scripts/backfill_official.py --start 2024-01-01 --end 2024-01-31 --verbose
    python scripts/backfill_official.py --start 2024-01-01 --end 2024-01-01 --skip-existing
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors import official_dl
from src.db.connection import connect as db_connect
from src.parsers.official_b import parse_b_text
from src.parsers.official_k import parse_k_text


def upsert_b(conn: sqlite3.Connection, parsed: list[dict]) -> tuple[int, int]:
    n_races = 0
    n_entries = 0
    for race in parsed:
        # race upsert (Layer2 で既にあれば上書きしないよう INSERT OR IGNORE)
        conn.execute("""
            INSERT OR IGNORE INTO races
                (race_id, race_date, stadium_number, race_number,
                 race_grade_number, race_title, race_subtitle,
                 race_distance, race_closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            race["race_id"], race["race_date"], race["stadium_number"],
            race["race_number"], race.get("race_grade_number"),
            race.get("race_title"), race.get("race_subtitle"),
            race.get("race_distance"), race.get("race_closed_at"),
        ))
        # ユーザ要望 (2026-05-19): B file から抽出した「電話投票締切予定」
        # を既存 race shell の NULL closed_at にだけ書き込む。
        # Open API で既に closed_at が入っていれば触らない (Open API が「正」)。
        if race.get("race_closed_at"):
            conn.execute(
                "UPDATE races SET race_closed_at = ? "
                "WHERE race_id = ? AND race_closed_at IS NULL",
                (race["race_closed_at"], race["race_id"]),
            )
        n_races += 1
        for boat in race["boats"]:
            conn.execute("""
                INSERT OR IGNORE INTO race_entries (
                    race_id, boat_number, racer_number, racer_name,
                    class_number, branch_number, birthplace_number,
                    age, weight, flying_count, late_count, avg_start_timing,
                    national_top_1_percent, national_top_2_percent, national_top_3_percent,
                    local_top_1_percent, local_top_2_percent, local_top_3_percent,
                    assigned_motor_number, assigned_motor_top_2_percent, assigned_motor_top_3_percent,
                    assigned_boat_number, assigned_boat_top_2_percent, assigned_boat_top_3_percent
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                race["race_id"], boat["boat_number"], boat["racer_number"],
                boat.get("racer_name"), boat.get("class_number"),
                None, None,  # branch_number, birthplace_number (B からは取れない)
                boat.get("age"), boat.get("weight"),
                boat.get("flying_count"), boat.get("late_count"), boat.get("avg_start_timing"),
                boat.get("national_top_1_percent"), boat.get("national_top_2_percent"), None,
                boat.get("local_top_1_percent"), boat.get("local_top_2_percent"), None,
                boat.get("assigned_motor_number"), boat.get("assigned_motor_top_2_percent"), None,
                boat.get("assigned_boat_number"), boat.get("assigned_boat_top_2_percent"), None,
            ))
            n_entries += 1
    return n_races, n_entries


def upsert_k(conn: sqlite3.Connection, parsed: list[dict]) -> tuple[int, int, int]:
    n_results = 0
    n_payouts = 0
    n_previews = 0
    for race in parsed:
        rid = race["race_id"]
        # races (Layer2 で無ければ最低限の行を作る)
        conn.execute("""
            INSERT OR IGNORE INTO races
                (race_id, race_date, stadium_number, race_number)
            VALUES (?, ?, ?, ?)
        """, (rid, race["race_date"], race["stadium_number"], race["race_number"]))

        # 結果
        for r in race["results"]:
            conn.execute("""
                INSERT OR IGNORE INTO race_results (
                    race_id, boat_number, finishing_position,
                    course_number, start_timing, race_time, remarks
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                rid, r["boat_number"], r.get("finishing_position"),
                r.get("course_number"), r.get("start_timing"),
                r.get("race_time"), r.get("remarks"),
            ))
            n_results += 1

        # 払戻
        for p in race["payouts"]:
            conn.execute("""
                INSERT OR IGNORE INTO race_payouts
                    (race_id, bet_type, combination, payout, popularity)
                VALUES (?, ?, ?, ?, ?)
            """, (rid, p["bet_type"], p["combination"], p["payout"], None))
            n_payouts += 1

        # 簡易 race_previews (風速・波高のみ)
        if race.get("wind_speed") is not None or race.get("wave_height") is not None:
            for r in race["results"]:
                conn.execute("""
                    INSERT OR IGNORE INTO race_previews
                        (race_id, boat_number, wind_speed, wave_height)
                    VALUES (?, ?, ?, ?)
                """, (rid, r["boat_number"],
                      race.get("wind_speed"), race.get("wave_height")))
                n_previews += 1
    return n_results, n_payouts, n_previews


def process_day(target_date: date, conn: sqlite3.Connection,
                logger: logging.Logger,
                skip_existing: bool = True) -> dict:
    """1日分: B/K ダウンロード → 解凍 → パース → DB投入"""
    summary = {
        "date": target_date.isoformat(),
        "b_races": 0, "b_entries": 0,
        "k_results": 0, "k_payouts": 0, "k_previews": 0,
        "skipped": False,
    }

    # 既に十分なデータがあればスキップ (Layer2 で取得済み等)
    if skip_existing:
        n_existing = conn.execute(
            "SELECT COUNT(*) FROM race_results r JOIN races ra ON r.race_id=ra.race_id WHERE ra.race_date=?",
            (target_date.isoformat(),)
        ).fetchone()[0]
        if n_existing > 200:  # 1日 144〜288 results
            summary["skipped"] = True
            return summary

    # B file
    b_txt = official_dl.fetch_one("B", target_date)
    if b_txt and b_txt.exists():
        try:
            b_parsed = parse_b_text(b_txt.read_bytes().decode("cp932", errors="replace"), target_date)
            n_r, n_e = upsert_b(conn, b_parsed)
            summary["b_races"] = n_r
            summary["b_entries"] = n_e
        except Exception as e:
            logger.exception("B parse/insert failed for %s: %s", target_date, e)

    # K file
    time.sleep(official_dl.DOWNLOAD_INTERVAL)
    k_txt = official_dl.fetch_one("K", target_date)
    if k_txt and k_txt.exists():
        try:
            k_parsed = parse_k_text(k_txt.read_bytes().decode("cp932", errors="replace"), target_date)
            n_res, n_pay, n_prev = upsert_k(conn, k_parsed)
            summary["k_results"] = n_res
            summary["k_payouts"] = n_pay
            summary["k_previews"] = n_prev
        except Exception as e:
            logger.exception("K parse/insert failed for %s: %s", target_date, e)

    conn.commit()
    return summary


def main():
    # --local フラグの場合は config import 前に DATABASE_URL を削除
    # (config.py が load_dotenv で .env から DATABASE_URL を再注入してしまうため)
    import os as _os
    if "--local" in sys.argv:
        _os.environ.pop("DATABASE_URL", None)
        _os.environ["DATABASE_URL"] = ""

    p = argparse.ArgumentParser()
    p.add_argument("--start", help="YYYY-MM-DD (--tomorrow 指定時は省略可)")
    p.add_argument("--end", help="YYYY-MM-DD (--tomorrow 指定時は省略可)")
    p.add_argument("--tomorrow", action="store_true",
                   help="明日 1 日分のみ取得 (run_daily_collect.bat 23:30 用)。"
                        "Open API は当日 0:00 過ぎまで公開されないため、Layer 1 "
                        "LZH (boatrace.jp 公式) で前日 23:00 過ぎに取得する。")
    p.add_argument("--local", action="store_true",
                   help="DATABASE_URL を無視してローカル SQLite に投入する "
                        "(daily_collect.py と同じ挙動)")
    p.add_argument("--skip-existing", action="store_true",
                   help="その日のresults数が十分なら処理スキップ")
    p.add_argument("--log-file", default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    # --tomorrow 指定時は start/end を翌日に自動設定
    if args.tomorrow:
        from datetime import timedelta as _td
        tomorrow = date.today() + _td(days=1)
        args.start = tomorrow.isoformat()
        args.end = tomorrow.isoformat()
    elif not (args.start and args.end):
        p.error("--start と --end は必須 (または --tomorrow を指定)")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    logger = logging.getLogger("backfill")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    days = (end - start).days + 1
    logger.info("backfill %s .. %s (%d days)", start, end, days)

    conn = db_connect()
    total = {
        "n_days": 0, "n_skipped": 0,
        "b_races": 0, "b_entries": 0,
        "k_results": 0, "k_payouts": 0, "k_previews": 0,
    }
    cur = start
    t0 = time.time()
    while cur <= end:
        s = process_day(cur, conn, logger, skip_existing=args.skip_existing)
        total["n_days"] += 1
        if s["skipped"]:
            total["n_skipped"] += 1
        else:
            total["b_races"] += s["b_races"]
            total["b_entries"] += s["b_entries"]
            total["k_results"] += s["k_results"]
            total["k_payouts"] += s["k_payouts"]
            total["k_previews"] += s["k_previews"]
        elapsed = time.time() - t0
        rate = total["n_days"] / elapsed if elapsed > 0 else 0
        eta_s = (days - total["n_days"]) / rate if rate > 0 else 0
        if total["n_days"] % 10 == 0 or args.verbose:
            print(f"  [{total['n_days']:4d}/{days}] {cur} "
                  f"b_races={s['b_races']} k_results={s['k_results']} "
                  f"skipped={s['skipped']}  ETA: {eta_s/60:.1f} min")
        cur = cur + timedelta(days=1)

    conn.close()
    print("\n=== 集計 ===")
    for k, v in total.items():
        print(f"  {k}: {v:,}")


if __name__ == "__main__":
    main()
