"""指定日のレース天候(weather_number/wind/wave 等) を公式結果ページで上書き。

朝の Open API や直前情報スクレイプの天候が現実と乖離していた場合に、
公式 raceresult ページに記載された確定値で race_previews を補正する。

背景 (2026-05-28):
  浜名湖12R が朝の weather_number=3 のまま live スクレイパーで更新されず、
  雨除外フラグでピックスリストから消えていた。本スクリプトで公式の
  確定値を取り直して上書きすると ROI 集計も正しい値に揃う。

使い方:
    python scripts/refresh_race_weather.py                       # 今日全レース
    python scripts/refresh_race_weather.py --date 2026-05-28     # 指定日
    python scripts/refresh_race_weather.py --stadium 6           # 浜名湖のみ
    python scripts/refresh_race_weather.py --dry-run             # 更新せず表示

実装上の注意:
  - 結果ページがまだ無い (未走 or 公式公開待ち) レースはスキップ。
  - race_previews へは weather_number/wind_speed/wind_direction/wave_height/
    temperature/water_temperature を COALESCE 上書き (None なら既存維持)。
  - DATABASE_URL があれば Supabase も併せて更新。
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import config
from src.collectors.result_scraper import scrape_race_result, overwrite_race_previews_weather

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

WX = {1: "晴", 2: "曇", 3: "雨", 4: "霧", 5: "雪"}


def _today() -> str:
    return date.today().isoformat()


def _local_conn():
    return sqlite3.connect(config.DB_PATH)


def _list_target_races(conn, target_date: str, stadium: int | None,
                       since_hours: float | None) -> list[tuple]:
    """対象レース (締切済 = 現在時刻 > race_closed_at) を抽出。
    since_hours: 指定するとそれ以内に締切ったレースだけに絞る (hourly 自動実行用)。
    """
    now = datetime.now()
    sql = """SELECT race_id, stadium_number, race_number, race_closed_at
               FROM races
              WHERE race_date=? AND race_closed_at < ?"""
    args: list = [target_date, now.strftime("%Y-%m-%d %H:%M:%S")]
    if stadium is not None:
        sql += " AND stadium_number=?"
        args.append(stadium)
    if since_hours is not None and since_hours > 0:
        cutoff = (now - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")
        sql += " AND race_closed_at >= ?"
        args.append(cutoff)
    sql += " ORDER BY stadium_number, race_number"
    return conn.execute(sql, args).fetchall()


def _current_weather(conn, race_id: str):
    row = conn.execute(
        "SELECT weather_number, wind_speed, wave_height, live_updated_at "
        "FROM race_previews WHERE race_id=? AND boat_number=1",
        (race_id,),
    ).fetchone()
    return row


def process(conn, label: str, races: list, dry_run: bool, interval: float) -> dict:
    summary = {"checked": 0, "scrape_ok": 0, "updated": 0, "no_data": 0, "no_change": 0}
    for race_id, sta, rno, closed in races:
        summary["checked"] += 1
        cur = _current_weather(conn, race_id)
        cur_w = cur[0] if cur else None
        cur_upd = cur[3] if cur else None
        try:
            payload = scrape_race_result(race_id)
        except Exception as e:
            logger.warning("scrape failed %s: %s", race_id, e)
            summary["no_data"] += 1
            _time.sleep(interval)
            continue
        if not payload:
            summary["no_data"] += 1
            _time.sleep(interval)
            continue
        summary["scrape_ok"] += 1
        weather = payload.get("weather") or {}
        new_w = weather.get("weather_number")
        change = (new_w is not None) and (cur_w != new_w)
        tag = " CHANGE" if change else ""
        logger.info(
            "[%s] %s s=%d R%d closed=%s  cur=%s(%s) upd=%s -> new=%s(%s)%s",
            label, race_id, sta, rno, closed,
            WX.get(cur_w, cur_w), cur_w, cur_upd,
            WX.get(new_w, new_w), new_w, tag,
        )
        if dry_run:
            if change:
                summary["updated"] += 1
            else:
                summary["no_change"] += 1
            _time.sleep(interval)
            continue
        n = overwrite_race_previews_weather(race_id, weather, conn)
        if change:
            summary["updated"] += 1
        else:
            summary["no_change"] += 1
        _time.sleep(interval)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=_today())
    parser.add_argument("--stadium", type=int, default=None,
                        help="特定会場のみ (例: 6=浜名湖)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="リクエスト間隔(秒)")
    parser.add_argument("--since-hours", type=float, default=None,
                        help="直近 N 時間以内に締切ったレースのみ (hourly 自動実行用)")
    args = parser.parse_args()

    print(f"=== refresh_race_weather {args.date}"
          f" stadium={args.stadium} since_hours={args.since_hours}"
          f" dry-run={args.dry_run} ===")

    # Local SQLite
    local = _local_conn()
    races = _list_target_races(local, args.date, args.stadium, args.since_hours)
    print(f"対象レース: {len(races)} 件")
    s_local = process(local, "LOCAL", races, args.dry_run, args.interval)
    local.close()
    print(f"[LOCAL] {s_local}")

    # Supabase (DATABASE_URL があれば)
    if os.getenv("DATABASE_URL", "").strip() and not args.dry_run:
        from src.db.connection import connect as db_connect
        pg = db_connect()
        # Supabase 側は既に LOCAL でスクレイプ済の payload を再利用したいが、
        # 現在は process() がスクレイプも内包しているため二重スクレイプになる。
        # BAN リスク低減のため interval を倍 (3秒) にして再実行。
        # TODO: 将来は scrape を分離して payload キャッシュを共有する。
        s_pg = process(pg, "SUPABASE", races, False, max(args.interval, 3.0))
        pg.close()
        print(f"[SUPABASE] {s_pg}")


if __name__ == "__main__":
    main()
