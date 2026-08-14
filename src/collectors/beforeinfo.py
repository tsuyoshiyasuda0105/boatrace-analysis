"""
Layer 3: 直前情報スクレイパー (部品交換 + 展示情報の補完)

- races テーブルにある race_id のうち、まだ race_parts が無いものを対象に取得
- HTML を data/raw/beforeinfo/ に保存（再処理用）
- race_parts: DELETE → INSERT で更新
- race_previews: 既存行の欠損列のみ UPDATE (Open API データを上書きしない)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

import config
from src.collectors._http import fetch_html
from src.db.connection import connect as db_connect
from src.parsers.beforeinfo import parse_beforeinfo

logger = logging.getLogger(__name__)


def _ensure_stable_plate_column(conn: sqlite3.Connection) -> None:
    """Add race_previews.stable_plate for existing DBs."""
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


def _save_raw_html(target_date: date, stadium: int, race_no: int, html: str) -> None:
    out_dir = config.BEFOREINFO_DIR / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{stadium:02d}_{race_no:02d}.html"
    out.write_text(html, encoding="utf-8")


def _list_target_races(
    conn: sqlite3.Connection,
    target_date: date,
    force: bool,
) -> list[tuple[str, int, int]]:
    """その日の race_id, stadium_number, race_number を返す（force=False なら未取得分のみ）"""
    if force:
        sql = """
            SELECT race_id, stadium_number, race_number
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
        """
    else:
        sql = """
            SELECT r.race_id, r.stadium_number, r.race_number
              FROM races r
             WHERE r.race_date = ?
               AND r.race_id NOT IN (SELECT DISTINCT race_id FROM race_parts)
             ORDER BY r.stadium_number, r.race_number
        """
    return list(conn.execute(sql, (target_date.isoformat(),)).fetchall())


def _upsert_parts(
    conn: sqlite3.Connection,
    race_id: str,
    boat_number: int,
    parts: Iterable[str],
) -> int:
    """1艇の部品交換情報を upsert（既存は全削除 → INSERT）。

    P0-3 ガード: パース結果が空の場合は DELETE を行わずスキップする。
    HTML 構造変化などでパーサーが空を返したとき、既存の正常データを
    空で上書きして再取得ループを誘発するのを防ぐ。
    """
    rows = [(race_id, boat_number, p) for p in dict.fromkeys(parts)]
    if not rows:
        existing = conn.execute(
            "SELECT COUNT(*) FROM race_parts WHERE race_id = ? AND boat_number = ?",
            (race_id, boat_number),
        ).fetchone()
        if existing and existing[0]:
            logger.warning(
                "parts parse returned empty for %s boat %d; keeping %d existing rows",
                race_id, boat_number, existing[0],
            )
        return 0
    conn.execute(
        "DELETE FROM race_parts WHERE race_id = ? AND boat_number = ?",
        (race_id, boat_number),
    )
    conn.executemany(
        "INSERT INTO race_parts (race_id, boat_number, part_code) VALUES (?, ?, ?)",
        rows,
    )
    return len(rows)


def _supplement_preview(
    conn: sqlite3.Connection,
    race_id: str,
    boat_number: int,
    boat_data: dict,
    page_data: dict,
) -> None:
    """
    race_previews の欠損列だけを UPDATE で補完。
    Open API 由来の値を Layer 3 で上書きしない (COALESCE で守る)。
    """
    # 行が無ければ INSERT
    exists = conn.execute(
        "SELECT 1 FROM race_previews WHERE race_id = ? AND boat_number = ?",
        (race_id, boat_number),
    ).fetchone()

    if not exists:
        conn.execute(
            """
            INSERT INTO race_previews (
                race_id, boat_number,
                weather_number, wind_speed, wind_direction_number,
                wave_height, temperature, water_temperature, stable_plate,
                course_number, exhibition_time, start_timing_exhibition,
                weight_adjustment, tilt_adjustment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id, boat_number,
                page_data.get("weather_number"),
                page_data.get("wind_speed"),
                page_data.get("wind_direction_number"),
                page_data.get("wave_height"),
                page_data.get("temperature"),
                page_data.get("water_temperature"),
                page_data.get("stable_plate"),
                boat_data.get("course_number"),
                boat_data.get("exhibition_time"),
                boat_data.get("start_timing_exhibition"),
                boat_data.get("weight_adjustment"),
                boat_data.get("tilt_adjustment"),
            ),
        )
        return

    # 既存行の NULL 列のみ Layer 3 値で埋める
    conn.execute(
        """
        UPDATE race_previews
           SET weather_number          = COALESCE(weather_number, ?),
               wind_speed              = COALESCE(wind_speed, ?),
               wind_direction_number   = COALESCE(wind_direction_number, ?),
               wave_height             = COALESCE(wave_height, ?),
               temperature             = COALESCE(temperature, ?),
               water_temperature       = COALESCE(water_temperature, ?),
               stable_plate            = COALESCE(?, stable_plate),
               course_number           = COALESCE(course_number, ?),
               exhibition_time         = COALESCE(NULLIF(exhibition_time, 0), ?),
               start_timing_exhibition = COALESCE(start_timing_exhibition, ?),
               weight_adjustment       = COALESCE(weight_adjustment, ?),
               tilt_adjustment         = COALESCE(tilt_adjustment, ?)
         WHERE race_id = ? AND boat_number = ?
        """,
        (
            page_data.get("weather_number"),
            page_data.get("wind_speed"),
            page_data.get("wind_direction_number"),
            page_data.get("wave_height"),
            page_data.get("temperature"),
            page_data.get("water_temperature"),
            page_data.get("stable_plate"),
            boat_data.get("course_number"),
            boat_data.get("exhibition_time"),
            boat_data.get("start_timing_exhibition"),
            boat_data.get("weight_adjustment"),
            boat_data.get("tilt_adjustment"),
            race_id, boat_number,
        ),
    )


def collect_for_date(
    target_date: date,
    db_path: Optional[str] = None,
    force: bool = False,
    save_html: bool = True,
) -> dict:
    """
    指定日の直前情報を一括取得。

    Args:
      target_date: 対象日
      db_path: DB パス (省略時 config.DB_PATH)
      force: True なら既取得分も再取得
      save_html: True なら data/raw/beforeinfo/ に HTML を保存

    Returns:
      {"date": ..., "races_targeted": int, "races_fetched": int,
       "parts_inserted": int, "previews_supplemented": int}
    """
    config.ensure_dirs()
    conn = db_connect(db_path)
    _ensure_stable_plate_column(conn)

    summary = {
        "date": target_date.isoformat(),
        "races_targeted": 0,
        "races_fetched": 0,
        "parts_inserted": 0,
        "previews_supplemented": 0,
    }

    try:
        targets = _list_target_races(conn, target_date, force)
        summary["races_targeted"] = len(targets)
        if not targets:
            logger.info("no target races for %s", target_date)
            return summary

        date_str = target_date.strftime("%Y%m%d")

        for race_id, stadium, race_no in targets:
            url = config.BEFOREINFO_URL.format(jcd=stadium, date=date_str, rno=race_no)
            html = fetch_html(url)
            if not html:
                logger.info("skip (no html): %s", race_id)
                continue

            if save_html:
                try:
                    _save_raw_html(target_date, stadium, race_no, html)
                except OSError as e:
                    logger.warning("html save failed %s: %s", race_id, e)

            page = parse_beforeinfo(html)
            summary["races_fetched"] += 1

            for boat in page.get("boats", []):
                bn = boat.get("boat_number")
                if not bn:
                    continue
                summary["parts_inserted"] += _upsert_parts(
                    conn, race_id, bn, boat.get("parts", [])
                )
                _supplement_preview(conn, race_id, bn, boat, page)
                summary["previews_supplemented"] += 1

            conn.commit()

        logger.info("beforeinfo %s: %s", target_date, summary)
        return summary
    finally:
        conn.close()
