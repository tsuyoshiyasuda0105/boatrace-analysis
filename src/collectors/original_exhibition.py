"""Collect venue-specific original exhibition data where available.

Many venue sites do not expose original exhibition data at all, and even when
they do, the table is often available only close to race time. This collector
tries conservative venue URL patterns and stores rows only when a clear
original exhibition table is found.
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

# Expand probing to every official venue domain. The collector still stores
# nothing unless the parser actually finds a usable table, so widening this map
# is a low-risk way to start collecting the venues that do expose the data.
VENUE_DOMAINS: dict[int, str] = {
    1: "boatrace-kiryu.jp",
    2: "boatrace-toda.jp",
    3: "boatrace-edogawa.com",
    4: "boatrace-heiwajima.net",
    5: "boatrace-tamagawa.com",
    6: "boatrace-hamanako.com",
    7: "boatrace-gamagori.jp",
    8: "boatrace-tokoname.jp",
    9: "boatrace-tsu.com",
    10: "boatrace-mikuni.jp",
    11: "boatrace-biwako.jp",
    12: "boatrace-suminoe.jp",
    13: "boatrace-amagasaki.jp",
    14: "boatrace-naruto.jp",
    15: "boatrace-marugame.jp",
    16: "boatrace-kojima.jp",
    17: "boatrace-miyajima.com",
    18: "boatrace-tokuyama.jp",
    19: "boatrace-shimonoseki.jp",
    20: "boatrace-wakamatsu.com",
    21: "boatrace-ashiya.com",
    22: "boatrace-fukuoka.com",
    23: "boatrace-karatsu.jp",
    24: "boatrace-omura.jp",
}


def _default_patterns(stadium: int) -> list[tuple[str, str]]:
    domain = VENUE_DOMAINS.get(stadium)
    if not domain:
        return []
    source_name = f"venue_{stadium:02d}_raceinfo"
    hosts = [f"www.{domain}", domain]
    patterns: list[tuple[str, str]] = []
    for host in hosts:
        patterns.extend(
            [
                (
                    source_name,
                    f"https://{host}/modules/raceinfo/?page=index_racejoho&target_day={{date}}&rno={{rno}}",
                ),
                (
                    source_name,
                    f"https://{host}/modules/raceinfo/?page=index_raceinfo&target_day={{date}}&rno={{rno}}",
                ),
                (
                    source_name,
                    f"https://{host}/modules/raceinfo/?page=index&target_day={{date}}&rno={{rno}}",
                ),
            ]
        )
    return patterns


_ALL_SOURCE_PATTERNS: dict[int, list[tuple[str, str]]] = {
    stadium: _default_patterns(stadium)
    for stadium in sorted(VENUE_DOMAINS)
}
_ALL_SOURCE_PATTERNS[3] = [
    ("edogawa_raceinfo", "https://www.boatrace-edogawa.com/modules/kouryaku/race_betsu.php?day={date}&rno={rno}"),
    ("edogawa_raceinfo", "https://www.boatrace-edogawa.com/modules/raceresult/index.php?day={date}&rno={rno}"),
    *_ALL_SOURCE_PATTERNS[3],
]

# Keep any hand-tuned patterns from the original map, but probe the full venue set.
for _stadium, _patterns in SOURCE_PATTERNS.items():
    _ALL_SOURCE_PATTERNS.setdefault(_stadium, [])
    for _pattern in _patterns:
        if _pattern not in _ALL_SOURCE_PATTERNS[_stadium]:
            _ALL_SOURCE_PATTERNS[_stadium].append(_pattern)
SOURCE_PATTERNS = _ALL_SOURCE_PATTERNS


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


def _attempt_dir() -> Path:
    return getattr(
        config,
        "ORIGINAL_EXHIBITION_ATTEMPT_DIR",
        config.RAW_DIR / "original_exhibition_attempts",
    )


def _save_raw_html(target_date: date, stadium: int, race_no: int, source_name: str, html: str) -> None:
    out_dir = _raw_dir() / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_source = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in source_name)
    out = out_dir / f"{stadium:02d}_{race_no:02d}_{safe_source}.html"
    out.write_text(html, encoding="utf-8")


def _save_attempt_html(target_date: date, stadium: int, race_no: int, source_name: str, html: str) -> None:
    out_dir = _attempt_dir() / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_source = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in source_name)
    out = out_dir / f"{stadium:02d}_{race_no:02d}_{safe_source}.html"
    out.write_text(html, encoding="utf-8")


def _list_target_races(
    conn,
    target_date: date,
    force: bool,
    stadium_filter: set[int] | None = None,
) -> list[tuple[str, int, int]]:
    stadiums = tuple(sorted(stadium_filter or SOURCE_PATTERNS.keys()))
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


def _collect_targets(
    conn,
    target_date: date,
    targets: list[tuple[str, int, int]],
    save_html: bool,
    save_attempted_html: bool = False,
    pattern_limit: int | None = None,
) -> dict:
    summary = {
        "date": target_date.isoformat(),
        "races_targeted": len(targets),
        "pages_fetched": 0,
        "pages_archived": 0,
        "races_found": 0,
        "rows_inserted": 0,
    }
    date_compact = target_date.strftime("%Y%m%d")
    for race_id, stadium, race_no in targets:
        patterns = SOURCE_PATTERNS.get(stadium, [])
        if pattern_limit and pattern_limit > 0:
            patterns = patterns[:pattern_limit]
        for source_name, pattern in patterns:
            url = pattern.format(date=date_compact, rno=race_no)
            html = fetch_html(url)
            if not html:
                continue
            summary["pages_fetched"] += 1
            if save_attempted_html:
                try:
                    _save_attempt_html(target_date, stadium, race_no, source_name, html)
                    summary["pages_archived"] += 1
                except OSError as exc:
                    logger.warning("original exhibition attempt html save failed %s: %s", race_id, exc)
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
                      save_html: bool = True,
                      save_attempted_html: bool = False,
                      stadiums: set[int] | None = None,
                      pattern_limit: int | None = None) -> dict:
    config.ensure_dirs()
    with db_connect(db_path) as conn:
        ensure_schema(conn)
        targets = [
            (race_id, stadium, race_no)
            for race_id, stadium, race_no in races
            if stadium in SOURCE_PATTERNS and (not stadiums or stadium in stadiums)
        ]
        targets = _filter_missing(conn, targets, force)
        return _collect_targets(
            conn,
            target_date,
            targets,
            save_html,
            save_attempted_html,
            pattern_limit,
        )


def collect_for_date(target_date: date, db_path: str | None = None, force: bool = False,
                     save_html: bool = True,
                     save_attempted_html: bool = False,
                     stadiums: set[int] | None = None,
                     pattern_limit: int | None = None) -> dict:
    config.ensure_dirs()
    with db_connect(db_path) as conn:
        ensure_schema(conn)
        targets = _list_target_races(conn, target_date, force, stadiums)
        return _collect_targets(
            conn,
            target_date,
            targets,
            save_html,
            save_attempted_html,
            pattern_limit,
        )
