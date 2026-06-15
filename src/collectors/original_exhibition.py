"""Collect venue-specific original exhibition data where available.

The source sites differ by venue and often only expose the table close to race
time. This collector tries known venue URL patterns and stores rows only when a
clear original exhibition table is found.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import config
from src.collectors._http import fetch_html
from src.db.connection import connect as db_connect
from src.parsers.original_exhibition import parse_original_exhibition

logger = logging.getLogger(__name__)


SOURCE_PATTERNS: dict[int, list[tuple[str, str]]] = {
    # Amagasaki introduced original exhibition times (1周 / まわり足) in 2021.
    13: [
        ("amagasaki_raceinfo", "https://www.boatrace-amagasaki.jp/modules/raceinfo/?page=index_racejoho&target_day={date}&rno={rno}"),
        ("amagasaki_raceinfo", "https://www.boatrace-amagasaki.jp/modules/raceinfo/?page=index_raceinfo&target_day={date}&rno={rno}"),
        ("amagasaki_raceinfo", "https://www.boatrace-amagasaki.jp/modules/raceinfo/?page=index&target_day={date}&rno={rno}"),
    ],
    # Tsu and Edogawa pages are kept as candidates. Some dates only expose
    # archive/text pages, so rows are saved only when the parser finds a table.
    9: [
        ("tsu_raceinfo", "https://www.boatrace-tsu.com/modules/raceinfo/?page=index_racejoho&target_day={date}&rno={rno}"),
        ("tsu_raceinfo", "https://www.boatrace-tsu.com/modules/raceinfo/?page=index_raceinfo&target_day={date}&rno={rno}"),
        ("tsu_raceinfo", "https://www.boatrace-tsu.com/modules/raceinfo/?page=index&target_day={date}&rno={rno}"),
    ],
    3: [
        ("edogawa_raceinfo", "https://www.boatrace-edogawa.com/modules/kouryaku/race_betsu.php?day={date}&rno={rno}"),
        ("edogawa_raceinfo", "https://www.boatrace-edogawa.com/modules/raceresult/index.php?day={date}&rno={rno}"),
    ],
}


def _execute_ddl(conn, sql: str) -> None:
    try:
        conn.execute(sql)
    except Exception as exc:
        logger.debug("DDL skipped/failed: %s", exc)


def ensure_schema(conn) -> None:
    _execute_ddl(
        conn,
        """
        CREATE TABLE IF NOT EXISTS race_original_exhibitions (
          race_id            TEXT NOT NULL,
          boat_number        INTEGER NOT NULL,
          source_name        TEXT NOT NULL,
          stadium_number     INTEGER,
          race_date          TEXT,
          race_number        INTEGER,
          lap_time           REAL,
          turn_time          REAL,
          straight_time      REAL,
          original_rank      INTEGER,
          raw_text           TEXT,
          source_url         TEXT,
          collected_at       TEXT,
          PRIMARY KEY (race_id, boat_number, source_name)
        )
        """,
    )
    _execute_ddl(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_original_exhibitions_race ON race_original_exhibitions(race_id)",
    )
    conn.commit()


def _raw_dir() -> Path:
    return getattr(config, "ORIGINAL_EXHIBITION_DIR", config.RAW_DIR / "original_exhibition")


def _save_raw_html(target_date: date, stadium: int, race_no: int, source_name: str, html: str) -> None:
    out_dir = _raw_dir() / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_source = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in source_name)
    out = out_dir / f"{stadium:02d}_{race_no:02d}_{safe_source}.html"
    out.write_text(html, encoding="utf-8")


def _list_target_races(conn, target_date: date, force: bool) -> list[tuple[str, int, int]]:
    stadiums = tuple(SOURCE_PATTERNS.keys())
    placeholders = ",".join("?" for _ in stadiums)
    if force:
        sql = f"""
            SELECT race_id, stadium_number, race_number
              FROM races
             WHERE race_date = ?
               AND stadium_number IN ({placeholders})
             ORDER BY stadium_number, race_number
        """
        params = (target_date.isoformat(), *stadiums)
    else:
        sql = f"""
            SELECT r.race_id, r.stadium_number, r.race_number
              FROM races r
             WHERE r.race_date = ?
               AND r.stadium_number IN ({placeholders})
               AND r.race_id NOT IN (
                   SELECT DISTINCT race_id FROM race_original_exhibitions
               )
             ORDER BY r.stadium_number, r.race_number
        """
        params = (target_date.isoformat(), *stadiums)
    return list(conn.execute(sql, params).fetchall())


def _upsert_rows(
    conn,
    race_id: str,
    stadium: int,
    race_date: str,
    race_no: int,
    source_name: str,
    source_url: str,
    rows: Iterable[dict],
) -> int:
    now_iso = datetime.now().isoformat(timespec="seconds")
    payload = []
    for row in rows:
        payload.append(
            (
                race_id,
                row.get("boat_number"),
                source_name,
                stadium,
                race_date,
                race_no,
                row.get("lap_time"),
                row.get("turn_time"),
                row.get("straight_time"),
                row.get("original_rank"),
                row.get("raw_text"),
                source_url,
                now_iso,
            )
        )
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO race_original_exhibitions (
            race_id, boat_number, source_name, stadium_number, race_date,
            race_number, lap_time, turn_time, straight_time, original_rank,
            raw_text, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _filter_missing(conn, targets: list[tuple[str, int, int]], force: bool) -> list[tuple[str, int, int]]:
    if force:
        return targets
    out = []
    for race_id, stadium, race_no in targets:
        exists = conn.execute(
            "SELECT 1 FROM race_original_exhibitions WHERE race_id = ? LIMIT 1",
            (race_id,),
        ).fetchone()
        if not exists:
            out.append((race_id, stadium, race_no))
    return out


def _collect_targets(conn, target_date: date, targets: list[tuple[str, int, int]],
                     save_html: bool) -> dict:
    summary = {
        "date": target_date.isoformat(),
        "races_targeted": len(targets),
        "pages_fetched": 0,
        "races_found": 0,
        "rows_inserted": 0,
    }
    date_compact = target_date.strftime("%Y%m%d")
    for race_id, stadium, race_no in targets:
        for source_name, pattern in SOURCE_PATTERNS.get(stadium, []):
            url = pattern.format(date=date_compact, rno=race_no)
            html = fetch_html(url)
            if not html:
                continue
            summary["pages_fetched"] += 1
            rows = parse_original_exhibition(html)
            if not rows:
                continue
            if save_html:
                try:
                    _save_raw_html(target_date, stadium, race_no, source_name, html)
                except OSError as exc:
                    logger.warning("original exhibition html save failed %s: %s", race_id, exc)
            n = _upsert_rows(
                conn, race_id, stadium, target_date.isoformat(), race_no,
                source_name, url, rows,
            )
            conn.commit()
            summary["races_found"] += 1
            summary["rows_inserted"] += n
            break
    return summary


def collect_for_races(target_date: date, races: Iterable[tuple[str, int, int]],
                      db_path: str | None = None, force: bool = False,
                      save_html: bool = True) -> dict:
    config.ensure_dirs()
    with db_connect(db_path) as conn:
        ensure_schema(conn)
        targets = [
            (race_id, stadium, race_no)
            for race_id, stadium, race_no in races
            if stadium in SOURCE_PATTERNS
        ]
        targets = _filter_missing(conn, targets, force)
        return _collect_targets(conn, target_date, targets, save_html)


def collect_for_date(target_date: date, db_path: str | None = None, force: bool = False,
                     save_html: bool = True) -> dict:
    config.ensure_dirs()
    with db_connect(db_path) as conn:
        ensure_schema(conn)
        targets = _list_target_races(conn, target_date, force)
        return _collect_targets(conn, target_date, targets, save_html)
