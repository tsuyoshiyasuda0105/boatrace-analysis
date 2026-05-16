"""直前情報ライブスクレイパー (動的天候・風・波の取り込み)

目的:
  Open API (`boatraceopenapi.github.io/previews/.../<date>.json`) は 1日1回
  しか更新されないため、レース当日の天候変化 (晴 → 雨、風弱 → 風強 等)
  をリアルタイムには取れない。
  boatrace.jp の `beforeinfo` ページは各レース 10〜20分前に最新値が公開
  されるので、そこから「締切まで 5〜30 分のレース」だけ取りに行く。

特徴:
  - **OVERWRITE**: 通常の collectors/beforeinfo.py は COALESCE で
    Open API 値を守るが、本スクリプトは volatile 値 (wind/wave/weather)
    を上書きする。直前情報の方が新しいため。
  - **smart filter**: 締切まで 5〜30 分のレースのみ取得、かつ過去
    `--cooldown-min` 分以内に取得済みなら skip。BAN リスク軽減。
  - **dual-write**: 本番 Supabase と local SQLite の両方に書く
    (local は cache_predictions の入力として必要)。
  - **再予測**: 変更があった場合、 cache_predictions_for_date(today)
    を実行 → Supabase へ predictions を sync。

スケジューリング想定:
  Windows Task Scheduler から 10分毎に起動 (08:00-22:00)。
  毎分起動だと BAN リスク × predict 計算コストが見合わない。

使い方:
    python scripts/scrape_beforeinfo_live.py
    python scripts/scrape_beforeinfo_live.py --dry-run     # 取得して表示のみ
    python scripts/scrape_beforeinfo_live.py --no-predict  # スクレイプのみ
    python scripts/scrape_beforeinfo_live.py --window 5 60 # 5-60分前を対象
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows cp932 で絵文字を出力できるよう UTF-8 化 (送信処理側の慣習に合わせる)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import config
from src.collectors._http import fetch_html
from src.db.connection import connect as db_connect
from src.parsers.beforeinfo import parse_beforeinfo

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))


# Volatile な列だけ更新する (Open API データを「上書き」する対象)
# 注: course_number / exhibition_time / start_timing_exhibition / parts は
# 直前情報固有の値なので通常 collector で扱う。本スクリプトは「天候・水面」のみ。
_VOLATILE_COLS = ("weather_number", "wind_speed", "wind_direction_number",
                  "wave_height", "temperature", "water_temperature")


def _parse_close_jst(closed_at, race_date) -> datetime | None:
    if isinstance(closed_at, datetime):
        return closed_at.replace(tzinfo=JST) if closed_at.tzinfo is None else closed_at
    if not isinstance(closed_at, str):
        return None
    try:
        if " " in closed_at and len(closed_at) >= 16:
            t = datetime.fromisoformat(closed_at)
        else:
            time_part = closed_at if len(closed_at) >= 5 else f"{closed_at}:00"
            rd_str = race_date.isoformat() if hasattr(race_date, "isoformat") else str(race_date)
            t = datetime.fromisoformat(f"{rd_str} {time_part}")
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=JST)


def find_due_races(now_jst: datetime, window_min: int, window_max: int,
                   cooldown_min: int) -> list[tuple[str, int, int, datetime]]:
    """締切まで [window_min, window_max] 分のレースを返す。
    過去 cooldown_min 分以内に live scrape されていれば除外。

    取得済 marker: race_previews.live_updated_at (新規列、自動作成)
    """
    target_date = now_jst.date().isoformat()
    with db_connect() as conn:
        # live_updated_at 列が無ければ追加 (idempotent)
        _ensure_live_column(conn)

        rows = conn.execute(
            """
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at
              FROM races r
             WHERE r.race_date = ?
               AND r.race_closed_at IS NOT NULL
            """,
            (target_date,),
        ).fetchall()

        # 最終更新時刻を bulk 取得
        seen = {}
        for rid, ts in conn.execute(
            """SELECT race_id, MAX(live_updated_at) FROM race_previews
                WHERE live_updated_at IS NOT NULL GROUP BY race_id"""
        ).fetchall():
            seen[rid] = ts

    due = []
    for rid, stadium, rno, closed_at in rows:
        close = _parse_close_jst(closed_at, target_date)
        if close is None:
            continue
        mins_until = (close - now_jst).total_seconds() / 60.0
        if not (window_min <= mins_until <= window_max):
            continue
        last_ts = seen.get(rid)
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts).replace(tzinfo=JST)
                age_min = (now_jst - last_dt).total_seconds() / 60.0
                if age_min < cooldown_min:
                    continue
            except (ValueError, TypeError):
                pass
        due.append((rid, stadium, rno, close))
    return due


def _ensure_live_column(conn) -> None:
    """race_previews.live_updated_at が無ければ追加 (SQLite/Postgres 共通)。"""
    try:
        # 試しに SELECT してみる
        conn.execute("SELECT live_updated_at FROM race_previews LIMIT 1")
    except Exception:
        try:
            conn.execute("ALTER TABLE race_previews ADD COLUMN live_updated_at TEXT")
            conn.commit()
            logger.info("added race_previews.live_updated_at column")
        except Exception as e:
            # already exists (race condition) or no permission
            logger.debug("ALTER COLUMN failed (likely already exists): %s", e)
            try:
                conn.rollback()
            except Exception:
                pass


def _update_volatile(conn, race_id: str, page_data: dict, now_iso: str) -> int:
    """race_previews の volatile 列だけを **上書き** UPDATE する。
    Returns: 影響行数 (通常 6 艇 = 6 行、無ければ 0)。
    """
    # 全 boat に同じ天候値を書く (race level data なので)
    weather = page_data.get("weather_number")
    wind = page_data.get("wind_speed")
    wind_dir = page_data.get("wind_direction_number")
    wave = page_data.get("wave_height")
    temp = page_data.get("temperature")
    wtemp = page_data.get("water_temperature")

    # COALESCE(?, col): 新値が NULL なら旧値を保持。直前情報パーサーが
    # weather_number を取り逃がしても Open API 朝値を消さない安全策。
    cur = conn.execute(
        """
        UPDATE race_previews
           SET weather_number        = COALESCE(?, weather_number),
               wind_speed            = COALESCE(?, wind_speed),
               wind_direction_number = COALESCE(?, wind_direction_number),
               wave_height           = COALESCE(?, wave_height),
               temperature           = COALESCE(?, temperature),
               water_temperature     = COALESCE(?, water_temperature),
               live_updated_at       = ?
         WHERE race_id = ?
        """,
        (weather, wind, wind_dir, wave, temp, wtemp, now_iso, race_id),
    )
    try:
        return cur.rowcount or 0
    except Exception:
        return 0


def scrape_one_race(stadium: int, race_no: int, target_date: date, dry_run: bool = False
                    ) -> dict | None:
    """1 レース分の beforeinfo を取得 → dict を返す (失敗時 None)。"""
    date_str = target_date.strftime("%Y%m%d")
    url = config.BEFOREINFO_URL.format(jcd=stadium, date=date_str, rno=race_no)
    html = fetch_html(url)
    if not html:
        logger.warning("beforeinfo no html: %s/%s/%s", stadium, race_no, date_str)
        return None
    try:
        page = parse_beforeinfo(html)
    except Exception as e:
        logger.error("parse failed %02d-%02d: %s", stadium, race_no, e)
        return None
    return page


def write_updates(updates: list[tuple[str, dict]], now_iso: str,
                  also_local: bool = True) -> dict:
    """変更を DB (Supabase + local) の両方に書く。
    Returns: {"supabase_rows": int, "local_rows": int, "races": int}
    """
    summary = {"supabase_rows": 0, "local_rows": 0, "races": 0}

    # === A. デフォルト接続 (本番 = Supabase / 開発 = SQLite) ===
    try:
        with db_connect() as conn:
            _ensure_live_column(conn)
            for race_id, page in updates:
                n = _update_volatile(conn, race_id, page, now_iso)
                summary["supabase_rows"] += n
                if n > 0:
                    summary["races"] += 1
            conn.commit() if hasattr(conn, "commit") else None
    except Exception as e:
        logger.error("Supabase update failed: %s", e)

    # === B. local SQLite 直接 (DATABASE_URL を一時的に外す) ===
    # cache_predictions が local SQLite を読むため、local も更新必須。
    if also_local:
        saved = os.environ.pop("DATABASE_URL", None)
        try:
            conn = sqlite3.connect(config.DB_PATH)
            _ensure_live_column(conn)
            for race_id, page in updates:
                n = _update_volatile(conn, race_id, page, now_iso)
                summary["local_rows"] += n
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("local SQLite update failed: %s", e)
        finally:
            if saved:
                os.environ["DATABASE_URL"] = saved

    return summary


def run_repredict_and_sync() -> dict:
    """cache_predictions_for_date(today) + sync_predictions_to_supabase。
    Returns: {"races": int, "synced": int}
    """
    from scripts.cache_predictions import (
        cache_predictions_for_date, sync_predictions_to_supabase,
    )
    today = date.today().isoformat()
    t0 = _time.time()
    n_races = cache_predictions_for_date(today)
    t_predict = _time.time() - t0
    print(f"[re-predict] {n_races} races in {t_predict:.1f}s")

    t0 = _time.time()
    n_synced = sync_predictions_to_supabase()
    t_sync = _time.time() - t0
    print(f"[sync]       {n_synced} rows in {t_sync:.1f}s")
    return {"races": n_races, "synced": n_synced}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="取得して表示するだけ。DB に書かない、予測しない。")
    p.add_argument("--no-predict", action="store_true",
                   help="DB 書き込みは行うが、再予測 + sync をスキップ")
    p.add_argument("--window", nargs=2, type=int, default=[5, 30],
                   metavar=("MIN", "MAX"),
                   help="締切までの分数ウィンドウ (デフォルト 5-30 分)")
    p.add_argument("--cooldown-min", type=int, default=8,
                   help="同じレースを再取得する最短間隔 (分、デフォルト 8)")
    p.add_argument("--force-all", action="store_true",
                   help="ウィンドウ外も含め本日全レースを取得 (テスト用)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    now = datetime.now(tz=JST)
    print(f"[{now.strftime('%H:%M:%S')}] beforeinfo live scrape start "
          f"(window={args.window[0]}-{args.window[1]}min, cooldown={args.cooldown_min}min)")

    if args.force_all:
        # 本日全レースを対象
        target_date = now.date().isoformat()
        with db_connect() as conn:
            rows = conn.execute(
                """SELECT race_id, stadium_number, race_number, race_closed_at
                     FROM races WHERE race_date = ?
                     ORDER BY race_closed_at""",
                (target_date,),
            ).fetchall()
        due = []
        for rid, stadium, rno, closed_at in rows:
            close = _parse_close_jst(closed_at, now.date()) or now
            due.append((rid, stadium, rno, close))
    else:
        due = find_due_races(now, args.window[0], args.window[1], args.cooldown_min)

    print(f"  due races: {len(due)}")
    if not due:
        print("  (no races due, exiting)")
        return

    # スクレイプ実行
    updates = []
    for rid, stadium, rno, close in due:
        if args.verbose:
            print(f"    scrape {rid} (close {close.strftime('%H:%M')})")
        page = scrape_one_race(stadium, rno, now.date(), dry_run=args.dry_run)
        if page is None:
            continue
        if args.verbose or args.dry_run:
            print(f"      weather={page.get('weather_number')} "
                  f"wind={page.get('wind_speed')} wave={page.get('wave_height')} "
                  f"temp={page.get('temperature')} wtemp={page.get('water_temperature')}")
        if not args.dry_run:
            updates.append((rid, page))

    if args.dry_run:
        print(f"  [dry-run] would update {len(updates)} races. exiting.")
        return

    if not updates:
        print("  no valid pages, exiting")
        return

    now_iso = datetime.now().isoformat(timespec="seconds")
    s = write_updates(updates, now_iso)
    print(f"  written: supabase_rows={s['supabase_rows']} "
          f"local_rows={s['local_rows']} races_updated={s['races']}")

    if args.no_predict:
        print("  --no-predict 指定、再予測スキップ")
        return

    if s["races"] == 0:
        print("  no race actually changed, skip re-predict")
        return

    run_repredict_and_sync()


if __name__ == "__main__":
    main()
