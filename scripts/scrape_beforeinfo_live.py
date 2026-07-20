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
    python scripts/scrape_beforeinfo_live.py --window 5 9  # 5分前基準
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


# Volatile な列だけ更新する (Open API データを「上書き」する対象)。
# 天候・水面に加えて、直前で確定する展示T/ST/進入/チルトも艇別に補完する。
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


def _preview_is_incomplete(conn, race_id: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n_rows,
               SUM(CASE WHEN exhibition_time IS NOT NULL
                         AND exhibition_time != 0 THEN 1 ELSE 0 END) AS n_ex_time,
               SUM(CASE WHEN start_timing_exhibition IS NOT NULL THEN 1 ELSE 0 END) AS n_ex_st,
               SUM(CASE WHEN weather_number IS NOT NULL THEN 1 ELSE 0 END) AS n_weather,
               SUM(CASE WHEN wind_speed IS NOT NULL THEN 1 ELSE 0 END) AS n_wind
          FROM race_previews
         WHERE race_id = ?
        """,
        (race_id,),
    ).fetchone()
    if not row:
        return True
    n_rows, n_ex_time, n_ex_st, n_weather, n_wind = [int(v or 0) for v in row]
    return n_rows < 6 or n_ex_time < 6 or n_ex_st < 6 or n_weather < 1 or n_wind < 1


def _load_market_signal_race_ids(target_date: date) -> set[str]:
    """Return race_ids shown in today's market signal list.

    This intentionally reuses the web app evaluator so the beforeinfo fetch queue
    follows the same adopted-strategy logic as the ROI-high race screen.
    """
    try:
        from src.web.app import create_app

        app = create_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["is_member"] = True
        resp = client.get(f"/api/market-signals?date={target_date.isoformat()}")
        if resp.status_code != 200:
            logger.warning("market-signals returned status=%s", resp.status_code)
            return set()
        payload = resp.get_json(silent=True) or {}
        signals = payload.get("signals") or {}
        if isinstance(signals, dict):
            return {str(rid) for rid in signals.keys()}
        if isinstance(signals, list):
            return {str(item.get("race_id")) for item in signals if item.get("race_id")}
    except Exception as e:
        logger.warning("market-signals candidate load failed: %s", e)
    return set()


def find_market_candidate_races(now_jst: datetime, past_min: int,
                                future_min: int) -> list[tuple[str, int, int, datetime]]:
    target_date = now_jst.date().isoformat()
    race_ids = _load_market_signal_race_ids(now_jst.date())
    if not race_ids:
        return []
    with db_connect() as conn:
        _ensure_live_column(conn)
        placeholders = ",".join("?" for _ in race_ids)
        rows = conn.execute(
            f"""
            SELECT race_id, stadium_number, race_number, race_closed_at
              FROM races
             WHERE race_date = ?
               AND race_id IN ({placeholders})
             ORDER BY race_closed_at
            """,
            (target_date, *race_ids),
        ).fetchall()
        due = []
        for rid, stadium, rno, closed_at in rows:
            close = _parse_close_jst(closed_at, target_date)
            if close is None:
                continue
            mins_until = (close - now_jst).total_seconds() / 60.0
            if mins_until < -abs(past_min) or mins_until > future_min:
                continue
            if not _preview_is_incomplete(conn, rid):
                continue
            due.append((rid, stadium, rno, close))
    return due


def find_recent_incomplete_races(now_jst: datetime, past_min: int,
                                 future_min: int, limit: int) -> list[tuple[str, int, int, datetime]]:
    target_date = now_jst.date().isoformat()
    with db_connect() as conn:
        _ensure_live_column(conn)
        rows = conn.execute(
            """
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at
              FROM races r
             WHERE r.race_date = ?
               AND r.race_closed_at IS NOT NULL
             ORDER BY r.race_closed_at
            """,
            (target_date,),
        ).fetchall()
        due = []
        for rid, stadium, rno, closed_at in rows:
            close = _parse_close_jst(closed_at, target_date)
            if close is None:
                continue
            mins_until = (close - now_jst).total_seconds() / 60.0
            if mins_until < -abs(past_min) or mins_until > future_min:
                continue
            if not _preview_is_incomplete(conn, rid):
                continue
            due.append((rid, stadium, rno, close))
            if limit > 0 and len(due) >= limit:
                break
    return due


def _merge_due_races(*groups: list[tuple[str, int, int, datetime]]) -> list[tuple[str, int, int, datetime]]:
    merged: list[tuple[str, int, int, datetime]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            rid = item[0]
            if rid in seen:
                continue
            seen.add(rid)
            merged.append(item)
    return merged


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
    try:
        conn.execute("SELECT stable_plate FROM race_previews LIMIT 1")
    except Exception:
        try:
            conn.execute("ALTER TABLE race_previews ADD COLUMN stable_plate INTEGER")
            conn.commit()
            logger.info("added race_previews.stable_plate column")
        except Exception as e:
            logger.debug("stable_plate ALTER failed (likely already exists): %s", e)
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
    stable_plate = page_data.get("stable_plate")
    updated = 0

    for boat in page_data.get("boats", []) or []:
        boat_no = boat.get("boat_number")
        if not boat_no:
            continue
        exists = conn.execute(
            "SELECT 1 FROM race_previews WHERE race_id = ? AND boat_number = ?",
            (race_id, boat_no),
        ).fetchone()
        if exists:
            continue
        exhibition_time = boat.get("exhibition_time")
        if exhibition_time == 0:
            exhibition_time = None
        conn.execute(
            """
            INSERT INTO race_previews (
                race_id, boat_number,
                weather_number, wind_speed, wind_direction_number,
                wave_height, temperature, water_temperature,
                course_number, exhibition_time, start_timing_exhibition,
                weight_adjustment, tilt_adjustment, live_updated_at, stable_plate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                boat_no,
                weather,
                wind,
                wind_dir,
                wave,
                temp,
                wtemp,
                boat.get("course_number"),
                exhibition_time,
                boat.get("start_timing_exhibition"),
                boat.get("weight_adjustment"),
                boat.get("tilt_adjustment"),
                now_iso,
                stable_plate,
            ),
        )
        updated += 1

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
               stable_plate          = COALESCE(?, stable_plate),
               live_updated_at       = ?
         WHERE race_id = ?
        """,
        (weather, wind, wind_dir, wave, temp, wtemp, stable_plate, now_iso, race_id),
    )
    try:
        updated += cur.rowcount or 0
    except Exception:
        pass

    for boat in page_data.get("boats", []) or []:
        boat_no = boat.get("boat_number")
        if not boat_no:
            continue
        exhibition_time = boat.get("exhibition_time")
        if exhibition_time == 0:
            exhibition_time = None
        cur = conn.execute(
            """
            UPDATE race_previews
               SET course_number           = COALESCE(?, course_number),
                   exhibition_time         = COALESCE(?, exhibition_time),
                   start_timing_exhibition = COALESCE(?, start_timing_exhibition),
                   weight_adjustment       = COALESCE(?, weight_adjustment),
                   tilt_adjustment         = COALESCE(?, tilt_adjustment),
                   live_updated_at         = ?
             WHERE race_id = ? AND boat_number = ?
            """,
            (
                boat.get("course_number"),
                exhibition_time,
                boat.get("start_timing_exhibition"),
                boat.get("weight_adjustment"),
                boat.get("tilt_adjustment"),
                now_iso,
                race_id,
                boat_no,
            ),
        )
        try:
            updated += cur.rowcount or 0
        except Exception:
            pass
    return updated


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


def run_repredict_and_sync(supabase_only: bool = False) -> dict:
    """展示更新後の再予測。

    local/backtest 併用時:
      cache_predictions_for_date(today) + sync_predictions_to_supabase
    Supabase 直保存時(Render clone 想定):
      render_cache_predictions 側の Supabase 直書き経路を使う
    """
    today = date.today().isoformat()

    if supabase_only:
        from scripts.render_cache_predictions import cache_predictions_for_date as render_cache_predictions_for_date

        t0 = _time.time()
        n_races = render_cache_predictions_for_date(today)
        t_predict = _time.time() - t0
        print(f"[re-predict:pg] {n_races} races in {t_predict:.1f}s")
        return {"races": n_races, "synced": n_races}

    from scripts.cache_predictions import (
        cache_predictions_for_date, sync_predictions_to_supabase,
    )
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
    p.add_argument("--window", nargs=2, type=int, default=[5, 9],
                   metavar=("MIN", "MAX"),
                   help="締切までの分数ウィンドウ (デフォルト 5-9 分)")
    p.add_argument("--cooldown-min", type=int, default=8,
                   help="同じレースを再取得する最短間隔 (分、デフォルト 8)")
    p.add_argument("--force-all", action="store_true",
                   help="ウィンドウ外も含め本日全レースを取得 (テスト用)")
    p.add_argument("--supabase-only", action="store_true",
                   help="local SQLite へのミラー書込を行わず、DATABASE_URL 側だけを更新する")
    p.add_argument("--no-market-candidates", action="store_true",
                   help="Do not add ROI-high market candidates to the beforeinfo queue")
    p.add_argument("--market-past-min", type=int, default=360,
                   help="Retry incomplete ROI-high candidates for this many minutes after close")
    p.add_argument("--market-future-min", type=int, default=480,
                   help="Prefetch incomplete ROI-high candidates this many minutes before close")
    p.add_argument("--incomplete-past-min", type=int, default=900,
                   help="Retry recent races with incomplete weather/wind/exhibition rows")
    p.add_argument("--incomplete-future-min", type=int, default=20,
                   help="Prefetch near-future races with incomplete weather/wind/exhibition rows")
    p.add_argument("--incomplete-limit", type=int, default=24,
                   help="Maximum non-market incomplete races to add per run")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    supabase_only = args.supabase_only or os.getenv("BOATRACE_SUPABASE_ONLY", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

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
        if not args.no_market_candidates:
            market_due = find_market_candidate_races(
                now,
                past_min=args.market_past_min,
                future_min=args.market_future_min,
            )
            if market_due:
                print(f"  market candidate races needing beforeinfo: {len(market_due)}")
            incomplete_due = find_recent_incomplete_races(
                now,
                past_min=args.incomplete_past_min,
                future_min=args.incomplete_future_min,
                limit=args.incomplete_limit,
            )
            if incomplete_due:
                print(f"  recent incomplete races needing beforeinfo: {len(incomplete_due)}")
            due = _merge_due_races(market_due, due, incomplete_due)

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
    s = write_updates(updates, now_iso, also_local=not supabase_only)
    print(f"  written: supabase_rows={s['supabase_rows']} "
          f"local_rows={s['local_rows']} races_updated={s['races']}")

    if args.no_predict:
        print("  --no-predict 指定、再予測スキップ")
        return

    if s["races"] == 0:
        print("  no race actually changed, skip re-predict")
        return

    run_repredict_and_sync(supabase_only=supabase_only)


if __name__ == "__main__":
    main()
