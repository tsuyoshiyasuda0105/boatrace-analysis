"""
Layer 2: Open API データ取得

GitHub Pages 上で公開されている JSON API から、
出走表・直前情報・結果を取得し DB に格納する。

毎日 1日の終わりに実行することを想定:
    python scripts/daily_collect.py --date 2026-05-08
"""
from __future__ import annotations

import json
import sqlite3
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import requests

import config
from src.db.connection import connect as db_connect

logger = logging.getLogger(__name__)


# ============================================================
# 共通: HTTP取得
# ============================================================

def _fetch_json(url: str) -> Optional[dict]:
    """URLからJSONを取得。404時はNone (まだデータが無い場合)。"""
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT},
        )
    except requests.RequestException as e:
        logger.warning("取得失敗 %s: %s", url, e)
        return None

    if resp.status_code in (404, 503):
        logger.info("データ未公開 (%s): %s", resp.status_code, url)
        return None
    resp.raise_for_status()
    return resp.json()


def _save_raw(name: str, target_date: date, payload: dict) -> None:
    """生JSONを raw/openapi/ に保存（デバッグ・再処理用）"""
    out = config.OPENAPI_RAW_DIR / f"{target_date.isoformat()}_{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _race_id(race_date: str, stadium: int, race_no: int) -> str:
    """race_id 規約: 'YYYYMMDD-SS-RR'"""
    d = race_date.replace("-", "")
    return f"{d}-{stadium:02d}-{race_no:02d}"


# ============================================================
# Programs (出走表)
# ============================================================

def fetch_programs(target_date: date) -> Optional[dict]:
    url = config.OPENAPI_PROGRAMS_URL.format(
        year=target_date.year,
        date=target_date.strftime("%Y%m%d"),
    )
    data = _fetch_json(url)
    if data:
        _save_raw("programs", target_date, data)
    return data


def upsert_programs(conn: sqlite3.Connection, programs_payload: dict) -> int:
    """programs APIの結果を races / race_entries に投入"""
    races = programs_payload.get("programs", [])
    n_races = 0
    n_entries = 0
    n_skipped_holiday = 0

    for race in races:
        rid = _race_id(race["race_date"], race["race_stadium_number"], race["race_number"])

        # backlog item: 休催 (canceled/no racing) 検出
        # Open API は休催会場でも race shell を返してくるが、全 boats が
        # racer_number=None かつ race_closed_at=None の場合は実質「休催」を意味する。
        # 空 race shell が残ると check_data_quality.py の entries_complete が
        # 12 件エラー扱いになるため、シェル作成自体をスキップする。
        boats = race.get("boats", [])
        all_boats_null = all(b.get("racer_number") is None for b in boats) if boats else True
        if all_boats_null and race.get("race_closed_at") is None:
            logger.info(
                "skip race shell (休催 detected): race_id=%s "
                "(all boats null + closed_at null = stadium not racing this day)",
                rid,
            )
            n_skipped_holiday += 1
            continue

        # ユーザ要望 (2026-05-19): 翌朝の Open API バッチが前夜の Layer 1
        # 投入値を NULL で上書きしないように COALESCE upsert にする。
        # 旧 INSERT OR REPLACE: Open API が NULL を返すとその列が消える
        # 新 ON CONFLICT DO UPDATE SET col=COALESCE(EXCLUDED.col, races.col):
        #   新値が NOT NULL なら採用、NULL なら既存値保持
        conn.execute("""
            INSERT INTO races
                (race_id, race_date, stadium_number, race_number,
                 race_grade_number, race_title, race_subtitle,
                 race_distance, race_closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (race_id) DO UPDATE SET
                race_grade_number = COALESCE(EXCLUDED.race_grade_number, races.race_grade_number),
                race_title = COALESCE(EXCLUDED.race_title, races.race_title),
                race_subtitle = COALESCE(EXCLUDED.race_subtitle, races.race_subtitle),
                race_distance = COALESCE(EXCLUDED.race_distance, races.race_distance),
                race_closed_at = COALESCE(EXCLUDED.race_closed_at, races.race_closed_at)
        """, (
            rid,
            race["race_date"],
            race["race_stadium_number"],
            race["race_number"],
            race.get("race_grade_number"),
            race.get("race_title"),
            race.get("race_subtitle"),
            race.get("race_distance"),
            race.get("race_closed_at"),
        ))
        n_races += 1

        for boat in race.get("boats", []):
            # racer_number が null の場合はスキップ (当日朝のAPIデータ未確定対策)
            # NOT NULL 制約のためトランザクション全体が失敗するのを防ぐ。
            # この艇は後の hourly_task で API が更新された時に再取り込みされる。
            if boat.get("racer_number") is None:
                logger.warning(
                    "skip boat with null racer_number: race_id=%s boat_number=%s "
                    "(will retry on next collect)",
                    rid, boat.get("racer_boat_number"),
                )
                continue
            # COALESCE upsert (前夜 Layer 1 投入値を NULL で上書きしない)
            # Open API は通常全フィールド埋まっているが、稀に top_3 系が null
            # の場合に Layer 1 値 (top_3 は null だが top_1/2 は値) を保持する。
            conn.execute("""
                INSERT INTO race_entries (
                    race_id, boat_number, racer_number, racer_name,
                    class_number, branch_number, birthplace_number,
                    age, weight, flying_count, late_count, avg_start_timing,
                    national_top_1_percent, national_top_2_percent, national_top_3_percent,
                    local_top_1_percent, local_top_2_percent, local_top_3_percent,
                    assigned_motor_number, assigned_motor_top_2_percent, assigned_motor_top_3_percent,
                    assigned_boat_number, assigned_boat_top_2_percent, assigned_boat_top_3_percent
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (race_id, boat_number) DO UPDATE SET
                    racer_number = COALESCE(EXCLUDED.racer_number, race_entries.racer_number),
                    racer_name = COALESCE(EXCLUDED.racer_name, race_entries.racer_name),
                    class_number = COALESCE(EXCLUDED.class_number, race_entries.class_number),
                    branch_number = COALESCE(EXCLUDED.branch_number, race_entries.branch_number),
                    birthplace_number = COALESCE(EXCLUDED.birthplace_number, race_entries.birthplace_number),
                    age = COALESCE(EXCLUDED.age, race_entries.age),
                    weight = COALESCE(EXCLUDED.weight, race_entries.weight),
                    flying_count = COALESCE(EXCLUDED.flying_count, race_entries.flying_count),
                    late_count = COALESCE(EXCLUDED.late_count, race_entries.late_count),
                    avg_start_timing = COALESCE(EXCLUDED.avg_start_timing, race_entries.avg_start_timing),
                    national_top_1_percent = COALESCE(EXCLUDED.national_top_1_percent, race_entries.national_top_1_percent),
                    national_top_2_percent = COALESCE(EXCLUDED.national_top_2_percent, race_entries.national_top_2_percent),
                    national_top_3_percent = COALESCE(EXCLUDED.national_top_3_percent, race_entries.national_top_3_percent),
                    local_top_1_percent = COALESCE(EXCLUDED.local_top_1_percent, race_entries.local_top_1_percent),
                    local_top_2_percent = COALESCE(EXCLUDED.local_top_2_percent, race_entries.local_top_2_percent),
                    local_top_3_percent = COALESCE(EXCLUDED.local_top_3_percent, race_entries.local_top_3_percent),
                    assigned_motor_number = COALESCE(EXCLUDED.assigned_motor_number, race_entries.assigned_motor_number),
                    assigned_motor_top_2_percent = COALESCE(EXCLUDED.assigned_motor_top_2_percent, race_entries.assigned_motor_top_2_percent),
                    assigned_motor_top_3_percent = COALESCE(EXCLUDED.assigned_motor_top_3_percent, race_entries.assigned_motor_top_3_percent),
                    assigned_boat_number = COALESCE(EXCLUDED.assigned_boat_number, race_entries.assigned_boat_number),
                    assigned_boat_top_2_percent = COALESCE(EXCLUDED.assigned_boat_top_2_percent, race_entries.assigned_boat_top_2_percent),
                    assigned_boat_top_3_percent = COALESCE(EXCLUDED.assigned_boat_top_3_percent, race_entries.assigned_boat_top_3_percent)
            """, (
                rid,
                boat["racer_boat_number"],
                boat["racer_number"],
                boat.get("racer_name"),
                boat.get("racer_class_number"),
                boat.get("racer_branch_number"),
                boat.get("racer_birthplace_number"),
                boat.get("racer_age"),
                boat.get("racer_weight"),
                boat.get("racer_flying_count"),
                boat.get("racer_late_count"),
                boat.get("racer_average_start_timing"),
                boat.get("racer_national_top_1_percent"),
                boat.get("racer_national_top_2_percent"),
                boat.get("racer_national_top_3_percent"),
                boat.get("racer_local_top_1_percent"),
                boat.get("racer_local_top_2_percent"),
                boat.get("racer_local_top_3_percent"),
                boat.get("racer_assigned_motor_number"),
                boat.get("racer_assigned_motor_top_2_percent"),
                boat.get("racer_assigned_motor_top_3_percent"),
                boat.get("racer_assigned_boat_number"),
                boat.get("racer_assigned_boat_top_2_percent"),
                boat.get("racer_assigned_boat_top_3_percent"),
            ))
            n_entries += 1

    logger.info("Programs: %d レース / %d 出走 投入 (休催 skip: %d)",
                n_races, n_entries, n_skipped_holiday)
    return n_races


# ============================================================
# Previews (直前情報)
# ============================================================

def fetch_previews(target_date: date) -> Optional[dict]:
    url = config.OPENAPI_PREVIEWS_URL.format(
        year=target_date.year,
        date=target_date.strftime("%Y%m%d"),
    )
    data = _fetch_json(url)
    if data:
        _save_raw("previews", target_date, data)
    return data


def upsert_previews(conn: sqlite3.Connection, payload: dict) -> int:
    races = payload.get("previews", [])
    n = 0
    for race in races:
        rid = _race_id(race["race_date"], race["race_stadium_number"], race["race_number"])
        weather_number = race.get("race_weather_number")
        wind_speed = race.get("race_wind")
        wind_dir = race.get("race_wind_direction_number")
        wave = race.get("race_wave")
        temp = race.get("race_temperature")
        water_temp = race.get("race_water_temperature")

        boats = race.get("boats", {})
        # boats は API 仕様上 dict ('1', '2', ...) で来る
        if isinstance(boats, dict):
            boat_list = boats.values()
        else:
            boat_list = boats

        for boat in boat_list:
            conn.execute("""
                INSERT OR REPLACE INTO race_previews (
                    race_id, boat_number,
                    weather_number, wind_speed, wind_direction_number,
                    wave_height, temperature, water_temperature,
                    course_number, exhibition_time, start_timing_exhibition,
                    weight_adjustment, tilt_adjustment
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rid,
                boat["racer_boat_number"],
                weather_number, wind_speed, wind_dir,
                wave, temp, water_temp,
                boat.get("racer_course_number"),
                boat.get("racer_exhibition_time"),
                boat.get("racer_start_timing"),
                boat.get("racer_weight_adjustment"),
                boat.get("racer_tilt_adjustment"),
            ))
            n += 1
    logger.info("Previews: %d 出走 投入", n)
    return n


# ============================================================
# Results (結果)
# ============================================================

def fetch_results(target_date: date) -> Optional[dict]:
    url = config.OPENAPI_RESULTS_URL.format(
        year=target_date.year,
        date=target_date.strftime("%Y%m%d"),
    )
    data = _fetch_json(url)
    if data:
        _save_raw("results", target_date, data)
    return data


def upsert_results(conn: sqlite3.Connection, payload: dict) -> int:
    """
    Results API の構造はリポジトリ仕様に合わせて柔軟にパース。
    結果が無いレース (中止/不成立) はスキップ。
    """
    races = payload.get("results", [])
    n_results = 0
    n_payouts = 0

    for race in races:
        rid = _race_id(race["race_date"], race["race_stadium_number"], race["race_number"])

        # 着順 (キー名は API 仕様により 'boats' か 'results' のどちらかで来る想定)
        boat_results = race.get("boats", race.get("results", []))
        if isinstance(boat_results, dict):
            boat_results = list(boat_results.values())

        # 決まり手は race レベルで 1 つだけ持つ (1着艇の決まり手)。
        # boat ループ内で個別に取ろうとすると None になるので、race から取り出して
        # 1 着の行にのみ記録する。
        race_kimarite = race.get("race_kimarite") or race.get("kimarite")

        # Open API は payouts は出すが boats 配列が空 or place=null のまま
        # 残してくるケースがある (バッチ更新の遅延中)。
        # その場合、INSERT OR REPLACE で既存の Layer 3 スクレイプ結果を
        # NULL で上書きしてしまうので、boats が完全に NULL の race は skip。
        all_places_null = all(
            r.get("racer_place_number") is None for r in boat_results
        ) if boat_results else True
        if all_places_null:
            # boats 情報なし → race_results に触らない (既存値を保持)
            pass
        else:
            for r in boat_results:
                place = r.get("racer_place_number")
                # place=null の row は既存を保持するためスキップ
                if place is None:
                    continue
                is_winner = (place == 1 or place == "1")
                conn.execute("""
                    INSERT OR REPLACE INTO race_results (
                        race_id, boat_number, finishing_position,
                        course_number, start_timing, race_time, remarks, kimarite
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rid,
                    r.get("racer_boat_number"),
                    place,
                    r.get("racer_course_number"),
                    r.get("racer_start_timing"),
                    r.get("racer_race_time"),
                    r.get("racer_remarks"),
                    race_kimarite if is_winner else None,
                ))
                n_results += 1

        # 払戻金 (API構造に合わせてフィールド名を調整する想定)
        payouts = race.get("payouts", {})
        for bet_type, items in payouts.items():
            if not isinstance(items, list):
                continue
            for item in items:
                conn.execute("""
                    INSERT OR REPLACE INTO race_payouts
                        (race_id, bet_type, combination, payout, popularity)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    rid,
                    bet_type,
                    item.get("combination", ""),
                    item.get("payout", 0),
                    item.get("popularity"),
                ))
                n_payouts += 1

    logger.info("Results: %d 着順 / %d 払戻 投入", n_results, n_payouts)
    return n_results


# ============================================================
# 統合エントリーポイント
# ============================================================

def collect_all(target_date: date, db_path: str = None) -> dict:
    """指定日の出走表・直前情報・結果をすべて取得しDBに格納。"""
    db_path = db_path or config.DB_PATH
    config.ensure_dirs()

    conn = db_connect(db_path)
    summary = {"date": target_date.isoformat(), "programs": 0, "previews": 0, "results": 0}
    try:
        if (p := fetch_programs(target_date)):
            summary["programs"] = upsert_programs(conn, p)
        if (p := fetch_previews(target_date)):
            summary["previews"] = upsert_previews(conn, p)
        if (p := fetch_results(target_date)):
            summary["results"] = upsert_results(conn, p)
        conn.commit()
    finally:
        conn.close()

    return summary
