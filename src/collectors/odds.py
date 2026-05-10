"""
Layer 3: 三連単オッズスクレイパー

- races テーブルにある race_id のうち、まだ odds_trifecta が無いものを対象
- 1日1回の取得を想定 (途中オッズは取らない)
- is_final: race_results に該当 race_id が存在すれば 1、なければ 0
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import config
from src.collectors._http import fetch_html
from src.db.connection import connect as db_connect
from src.parsers.odds import parse_trifecta_odds

logger = logging.getLogger(__name__)


def _save_raw_html(target_date: date, stadium: int, race_no: int, html: str) -> None:
    out_dir = config.RAW_DIR / "odds3t" / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{stadium:02d}_{race_no:02d}.html"
    out.write_text(html, encoding="utf-8")


def _list_target_races(
    conn: sqlite3.Connection,
    target_date: date,
    force: bool,
) -> list[tuple[str, int, int]]:
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
               AND r.race_id NOT IN (SELECT DISTINCT race_id FROM odds_trifecta)
             ORDER BY r.stadium_number, r.race_number
        """
    return list(conn.execute(sql, (target_date.isoformat(),)).fetchall())


def _is_finalized(conn: sqlite3.Connection, race_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM race_results WHERE race_id = ? LIMIT 1",
        (race_id,),
    ).fetchone() is not None


def _upsert_odds(
    conn: sqlite3.Connection,
    race_id: str,
    odds_map: dict[str, float],
    recorded_at: str,
    is_final: int,
    snapshot_label: Optional[str] = None,
) -> int:
    if not odds_map:
        return 0
    if snapshot_label is None:
        snapshot_label = "final" if is_final else "intermediate"
    rows = [
        (race_id, comb, float(odds), is_final, recorded_at, snapshot_label)
        for comb, odds in odds_map.items()
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO odds_trifecta
            (race_id, combination, odds, is_final, recorded_at, snapshot_label)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def collect_one_race(
    race_id: str,
    snapshot_label: str,
    db_path: Optional[str] = None,
) -> dict:
    """単一レースのオッズを 1 回スナップショット取得"""
    config.ensure_dirs()
    conn = db_connect(db_path)
    recorded_at = datetime.utcnow().isoformat(timespec="seconds")
    summary = {"race_id": race_id, "snapshot_label": snapshot_label, "odds_inserted": 0}
    try:
        # race_id から jcd, date, rno を抽出
        date_str, jcd_str, rno_str = race_id.split("-")
        jcd = int(jcd_str)
        rno = int(rno_str)
        url = config.ODDS_TRIFECTA_URL.format(jcd=jcd, date=date_str, rno=rno)
        html = fetch_html(url)
        if not html:
            summary["error"] = "no html"
            return summary
        odds_map = parse_trifecta_odds(html)
        if not odds_map:
            summary["error"] = "no odds parsed"
            return summary
        is_final = 1 if _is_finalized(conn, race_id) else 0
        n = _upsert_odds(conn, race_id, odds_map, recorded_at, is_final, snapshot_label)
        summary["odds_inserted"] = n
        conn.commit()
    finally:
        conn.close()
    return summary


def collect_for_date(
    target_date: date,
    db_path: Optional[str] = None,
    force: bool = False,
    save_html: bool = True,
    snapshot_label: Optional[str] = None,
) -> dict:
    config.ensure_dirs()
    conn = db_connect(db_path)
    recorded_at = datetime.utcnow().isoformat(timespec="seconds")

    summary = {
        "date": target_date.isoformat(),
        "races_targeted": 0,
        "races_fetched": 0,
        "odds_inserted": 0,
    }

    try:
        targets = _list_target_races(conn, target_date, force)
        summary["races_targeted"] = len(targets)
        if not targets:
            logger.info("no target races for %s", target_date)
            return summary

        date_str = target_date.strftime("%Y%m%d")

        for race_id, stadium, race_no in targets:
            url = config.ODDS_TRIFECTA_URL.format(jcd=stadium, date=date_str, rno=race_no)
            html = fetch_html(url)
            if not html:
                logger.info("skip (no html): %s", race_id)
                continue

            if save_html:
                try:
                    _save_raw_html(target_date, stadium, race_no, html)
                except OSError as e:
                    logger.warning("html save failed %s: %s", race_id, e)

            odds_map = parse_trifecta_odds(html)
            if not odds_map:
                logger.info("no odds parsed: %s", race_id)
                continue

            is_final = 1 if _is_finalized(conn, race_id) else 0
            label = snapshot_label or ("final" if is_final else "intermediate")
            n = _upsert_odds(conn, race_id, odds_map, recorded_at, is_final, label)
            summary["races_fetched"] += 1
            summary["odds_inserted"] += n

            conn.commit()

        logger.info("odds %s: %s", target_date, summary)
        return summary
    finally:
        conn.close()
