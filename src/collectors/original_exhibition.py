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
    1: [
        (
            "kiryu_cyokuzen",
            "https://www.kiryu-kyotei.com/modules/yosou/cyokuzen.php?day={date}&race={rno}",
        ),
    ],
    5: [
        (
            "tamagawa_oriten",
            "https://boatrace-tamagawa.com/modules/yosou/oriten.php?day={date}&race={rno}",
        ),
    ],
    6: [
        (
            "hamanako_cyokuzen",
            "https://www.boatrace-hamanako.jp/modules/yosou/group-cyokuzen.php?day={date}&race={rno}&kind=2",
        ),
    ],
    11: [
        (
            "biwako_cyokuzen",
            "https://www.boatrace-biwako.jp/modules/yosou/cyokuzen.php?day={date}&race={rno}&kind=2",
        ),
    ],
    # Amagasaki introduced original exhibition times (1周 / まわり足) in 2021.
    13: [
        (
            "amagasaki_cyokuzen",
            "https://boatrace-amagasaki.jp/modules/yosou/group-cyokuzen.php?day={date}&race={rno}&kind=2",
        ),
        ("amagasaki_raceinfo", "https://www.boatrace-amagasaki.jp/modules/raceinfo/?page=index_racejoho&target_day={date}&rno={rno}"),
        ("amagasaki_raceinfo", "https://www.boatrace-amagasaki.jp/modules/raceinfo/?page=index_raceinfo&target_day={date}&rno={rno}"),
        ("amagasaki_raceinfo", "https://www.boatrace-amagasaki.jp/modules/raceinfo/?page=index&target_day={date}&rno={rno}"),
    ],
    17: [
        (
            "miyajima_kaisai_reload",
            "https://www.boatrace-miyajima.com/race_common/require/kaisai_reload.php?race={rno}&date={date}",
        ),
    ],
    18: [
        (
            "tokuyama_tenji_keisoku",
            "https://www.boatrace-tokuyama.jp/tenji-keisoku/m/?day={date}&race={rno}",
        ),
    ],
    19: [
        (
            "shimonoseki_group_cyokuzen",
            "https://www.boatrace-shimonoseki.jp/modules/yosou/group-cyokuzen.php?day={date}&race={rno}&kind=2",
        ),
    ],
    22: [
        (
            "fukuoka_tenji_info",
            "https://www.boatrace-fukuoka.com/modules/yosou/tenji_info.php?day={date}&race={rno}",
        ),
    ],
    10: [
        (
            "mikuni_cyokuzen",
            "https://www.boatrace-mikuni.jp/modules/yosou/group-cyokuzen.php?day={date}&race={rno}&kind=2",
        ),
    ],
    # Omura keeps race-by-race original exhibition values in its syussou
    # archive. The page includes lap, turn and straight times for all six boats.
    24: [
        (
            "omura_syussou",
            "https://www.omurakyotei.jp/yosou/sp/syussou/?day={date}&race={rno:02d}",
        ),
        (
            "omura_syussou",
            "https://omurakyotei.jp/yosou/sp/syussou/?day={date}&race={rno:02d}",
        ),
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
    24: "omurakyotei.jp",
}


def _default_patterns(stadium: int) -> list[tuple[str, str]]:
    # Generic probing across all venues made the Render cron spend time on dead
    # or unsupported raceinfo hosts. Keep only explicit venue-tested patterns
    # so live beforeinfo work stays responsive.
    return []


_ALL_SOURCE_PATTERNS: dict[int, list[tuple[str, str]]] = {
    stadium: _default_patterns(stadium)
    for stadium in sorted(VENUE_DOMAINS)
}
# Probe hand-tuned patterns before generic candidates. Confirmed venue adapters
# should not pay for several known-dead generic requests first.
for _stadium, _patterns in SOURCE_PATTERNS.items():
    _generic = _ALL_SOURCE_PATTERNS.setdefault(_stadium, [])
    _ALL_SOURCE_PATTERNS[_stadium] = [
        *_patterns,
        *(_pattern for _pattern in _generic if _pattern not in _patterns),
    ]
SOURCE_PATTERNS = _ALL_SOURCE_PATTERNS


# Fields confirmed from the most recent 60 days of production data. Empty sets
# are intentional: the former venue candidates returned unrelated pages or a
# dead host, so they must not be probed until a working source is verified.
VENUE_FIELD_CAPABILITIES: dict[int, frozenset[str]] = {
    1: frozenset({"turn", "straight"}),
    3: frozenset(),
    5: frozenset({"lap", "turn", "straight"}),
    6: frozenset({"lap", "turn", "straight"}),
    9: frozenset(),
    10: frozenset({"lap", "turn", "straight"}),
    11: frozenset({"lap", "turn", "straight"}),
    13: frozenset({"lap", "turn"}),
    16: frozenset(),
    17: frozenset({"lap", "turn", "straight"}),
    18: frozenset({"lap", "turn"}),
    19: frozenset({"lap", "turn", "straight"}),
    22: frozenset({"lap", "turn", "straight"}),
    24: frozenset({"lap", "turn", "straight"}),
}


def expected_fields(stadium: int) -> frozenset[str]:
    """Return fields a verified venue source is expected to provide."""
    return VENUE_FIELD_CAPABILITIES.get(int(stadium), frozenset())


def supported_stadiums() -> frozenset[int]:
    """Return venues with both a verified source and at least one field."""
    return frozenset(
        int(stadium)
        for stadium, patterns in SOURCE_PATTERNS.items()
        if patterns and expected_fields(int(stadium))
    )


def has_complete_expected_fields(
    stadium: int,
    original_rows: int,
    lap_rows: int,
    turn_rows: int,
    straight_rows: int,
) -> bool:
    """Return whether all six boats have every field provided by the venue."""
    if int(original_rows or 0) < 6:
        return False
    counts = {
        "lap": int(lap_rows or 0),
        "turn": int(turn_rows or 0),
        "straight": int(straight_rows or 0),
    }
    expected = expected_fields(stadium)
    return bool(expected) and all(counts[field] >= 6 for field in expected)


def _execute_ddl(conn, sql: str) -> None:
    try:
        conn.execute(sql)
    except Exception as exc:
        logger.debug("DDL skipped/failed: %s", exc)


def _existing_columns(conn, table_name: str) -> set[str]:
    if getattr(conn, "_kind", "sqlite") == "postgres":
        rows = conn.execute(
            """SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'public' AND table_name = ?""",
            (table_name,),
        ).fetchall()
        return {str(row[0]) for row in rows}
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


_POSTGRES_SCHEMA_READY = False


def ensure_schema(conn) -> None:
    global _POSTGRES_SCHEMA_READY
    is_postgres = getattr(conn, "_kind", "sqlite") == "postgres"
    if is_postgres and _POSTGRES_SCHEMA_READY:
        return
    if is_postgres:
        columns = _existing_columns(conn, "race_original_exhibitions")
        required = {
            "race_id", "boat_number", "source_name", "dash_mark", "turn_mark",
            "straight_mark", "motor_eval_points",
        }
        if required.issubset(columns):
            _POSTGRES_SCHEMA_READY = True
            return
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
          dash_mark          TEXT,
          turn_mark          TEXT,
          straight_mark      TEXT,
          motor_eval_points  INTEGER,
          raw_text           TEXT,
          source_url         TEXT,
          collected_at       TEXT,
          PRIMARY KEY (race_id, boat_number, source_name)
        )
        """,
    )
    columns = _existing_columns(conn, "race_original_exhibitions")
    optional_columns = {
        "dash_mark": "TEXT",
        "turn_mark": "TEXT",
        "straight_mark": "TEXT",
        "motor_eval_points": "INTEGER",
    }
    for column, column_type in optional_columns.items():
        if column not in columns:
            _execute_ddl(
                conn,
                f"ALTER TABLE race_original_exhibitions ADD COLUMN {column} {column_type}",
            )
    _execute_ddl(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_original_exhibitions_race ON race_original_exhibitions(race_id)",
    )
    conn.commit()
    if is_postgres:
        _POSTGRES_SCHEMA_READY = True


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
    stadiums = tuple(sorted(supported_stadiums() & (stadium_filter or supported_stadiums())))
    if not stadiums:
        return []
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
                row.get("dash_mark"),
                row.get("turn_mark"),
                row.get("straight_mark"),
                row.get("motor_eval_points"),
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
            dash_mark, turn_mark, straight_mark, motor_eval_points,
            raw_text, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _filter_missing(conn, targets: list[tuple[str, int, int]], force: bool) -> list[tuple[str, int, int]]:
    if force:
        return targets
    out = []
    for race_id, stadium, race_no in targets:
        if _original_exhibition_needs_backfill(conn, race_id, stadium):
            out.append((race_id, stadium, race_no))
    return out


def _original_exhibition_needs_backfill(conn, race_id: str, stadium: int) -> bool:
    """Return True only when a venue-provided field is missing for any boat."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT boat_number) AS original_rows,
               COUNT(DISTINCT CASE
                   WHEN lap_time IS NOT NULL AND lap_time != 0
                   THEN boat_number
               END) AS lap_rows,
               COUNT(DISTINCT CASE
                   WHEN turn_time IS NOT NULL AND turn_time != 0
                   THEN boat_number
               END) AS turn_rows,
               COUNT(DISTINCT CASE
                   WHEN straight_time IS NOT NULL AND straight_time != 0
                   THEN boat_number
               END) AS straight_rows
          FROM race_original_exhibitions
         WHERE race_id = ?
        """,
        (race_id,),
    ).fetchone()
    if not row:
        return True

    return not has_complete_expected_fields(stadium, *row)


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
            if stadium in supported_stadiums() and (not stadiums or stadium in stadiums)
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
