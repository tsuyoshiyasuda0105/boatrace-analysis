"""Build racer accident-point events and period stats from race results.

This is a local-first reconstruction layer.  It does not claim to be an
official BOATRACE accident-rate feed; instead it stores the source event,
the rule version used for scoring, and enough context to compare later
against official period figures if we can fetch them.

Usage:
  py -3 scripts/rebuild_racer_accident_stats.py --local
  py -3 scripts/rebuild_racer_accident_stats.py --local --from 2024-01-01 --to 2026-07-15
  py -3 scripts/rebuild_racer_accident_stats.py --local --dry-run
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
from src.db.connection import assert_safe_production_write, connect as db_connect


JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


RULE_VERSION = "official_table_2025_05_reconstructed_v2"
RAW_ACCIDENT_CODES = {"F", "L", "K0", "K1", "S0", "S1", "S2", "失", "妨", "転", "落"}

RAW_ACCIDENT_CODES |= {
    "U", "W", "X", "x",
    "s", "k", "u", "b", "c", "d", "e",
    "t", "r",
}

DEFAULT_POINT_RULES = [
    ("FL", "F/L", 20, 30, "2025-05-01", None, 100, "official_table_2025_05", "F/L. Yusho race uses yusho_points."),
    ("U", "U", 20, 20, "2025-05-01", None, 100, "official_table_2025_05", "20-point KRAW code U."),
    ("W", "W", 20, 20, "2025-05-01", None, 100, "official_table_2025_05", "20-point KRAW code W."),
    ("X", "X", 20, 20, "2025-05-01", None, 100, "official_table_2025_05", "20-point KRAW code X."),
    ("x", "x", 15, 15, "2025-05-01", None, 100, "official_table_2025_05", "15-point KRAW code x."),
    ("s", "s", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code s."),
    ("k", "k", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code k."),
    ("u", "u", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code u."),
    ("b", "b", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code b."),
    ("c", "c", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code c."),
    ("d", "d", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code d."),
    ("e", "e", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "10-point KRAW code e."),
    ("t", "t", 2, 2, "2025-05-01", None, 100, "official_table_2025_05", "2-point KRAW code t."),
    ("r", "r", 2, 2, "2025-05-01", None, 100, "official_table_2025_05", "2-point KRAW code r."),
    ("OBSTRUCTION", "obstruction", 15, 15, "2025-05-01", None, 100, "official_table_2025_05", "Obstruction disqualification."),
    ("K1", "racer-responsible absence", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "Racer-responsible absence."),
    ("S1", "racer-responsible disqualification", 10, 10, "2025-05-01", None, 100, "official_table_2025_05", "Racer-responsible disqualification."),
    ("S2", "obstruction disqualification", 15, 15, "2025-05-01", None, 100, "official_table_2025_05", "Obstruction disqualification."),
    ("K0", "non-responsible absence", 0, 0, "2025-05-01", None, 100, "official_table_2025_05", "Non-responsible absence."),
    ("S0", "non-responsible disqualification", 0, 0, "2025-05-01", None, 100, "official_table_2025_05", "Non-responsible disqualification."),
    ("MINOR_VIOLATION", "minor violation", 2, 2, "2025-05-01", None, 100, "official_table_2025_05", "Minor violation."),
    ("DISQ_UNKNOWN", "unknown disqualification", 10, 10, "2025-05-01", None, 10, "reconstructed_fallback", "Fallback until source-specific labels are decoded."),
]


@dataclass(frozen=True)
class AccidentEvent:
    race_id: str
    race_date: str
    stadium_number: int
    race_number: int
    racer_number: int
    boat_number: int
    course_number: Optional[int]
    class_number: Optional[int]
    event_code: str
    event_label: str
    accident_points: int
    is_yusho: int
    raw_remarks: str
    rule_version: str = RULE_VERSION


@dataclass(frozen=True)
class KRawUnmatched:
    file_name: str
    line_number: int
    race_date: str
    race_number: Optional[int]
    event_code: str
    boat_number: int
    racer_number: int
    reason: str
    detail_reason: str
    raw_line: str
    rule_version: str = RULE_VERSION


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    if getattr(conn, "_kind", "sqlite") == "postgres":
        columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = ?
                """,
                (table_name,),
            )
        }
    else:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_point_rules (
          rule_version        TEXT NOT NULL,
          event_code          TEXT NOT NULL,
          event_label         TEXT NOT NULL,
          base_points         INTEGER NOT NULL,
          yusho_points        INTEGER,
          applies_from        TEXT NOT NULL,
          applies_to          TEXT,
          priority            INTEGER NOT NULL DEFAULT 100,
          source_kind         TEXT NOT NULL DEFAULT 'official_table',
          note                TEXT,
          updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (rule_version, event_code, applies_from)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_external_snapshots (
          snapshot_date       TEXT NOT NULL,
          racer_number        INTEGER NOT NULL,
          period_start        TEXT,
          period_end          TEXT,
          starts_count        INTEGER,
          accident_points     INTEGER,
          accident_rate       REAL,
          accident_codes_raw  TEXT,
          source_url          TEXT,
          source_kind         TEXT NOT NULL DEFAULT 'external',
          raw_payload         TEXT,
          created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (snapshot_date, racer_number, source_kind)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_events (
          race_id          TEXT NOT NULL,
          racer_number     INTEGER NOT NULL,
          boat_number      INTEGER NOT NULL,
          race_date        TEXT NOT NULL,
          stadium_number   INTEGER NOT NULL,
          race_number      INTEGER NOT NULL,
          course_number    INTEGER,
          class_number     INTEGER,
          event_code       TEXT NOT NULL,
          event_label      TEXT NOT NULL,
          accident_points  INTEGER NOT NULL,
          is_yusho         INTEGER NOT NULL DEFAULT 0,
          raw_remarks      TEXT,
          rule_version     TEXT NOT NULL,
          created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (race_id, racer_number, event_code, rule_version)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_racer_accident_events_racer_date
          ON racer_accident_events(racer_number, race_date)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_period_stats (
          racer_number        INTEGER NOT NULL,
          period_year         INTEGER NOT NULL,
          period_half         INTEGER NOT NULL,
          period_start        TEXT NOT NULL,
          period_end          TEXT NOT NULL,
          starts_count        INTEGER NOT NULL DEFAULT 0,
          accident_events     INTEGER NOT NULL DEFAULT 0,
          accident_points     INTEGER NOT NULL DEFAULT 0,
          accident_rate       REAL,
          rule_version        TEXT NOT NULL,
          source_kind         TEXT NOT NULL DEFAULT 'reconstructed',
          updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (racer_number, period_year, period_half, period_end, rule_version, source_kind)
        )
        """
    )
    if getattr(conn, "_kind", "") == "postgres":
        conn.execute(
            """
            ALTER TABLE racer_accident_period_stats
              DROP CONSTRAINT IF EXISTS racer_accident_period_stats_pkey
            """
        )
        conn.execute(
            """
            ALTER TABLE racer_accident_period_stats
              ADD PRIMARY KEY (
                racer_number,
                period_year,
                period_half,
                period_end,
                rule_version,
                source_kind
              )
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_period_adjustments (
          racer_number       INTEGER NOT NULL,
          period_start       TEXT NOT NULL,
          period_end         TEXT NOT NULL,
          adjustment_points  INTEGER NOT NULL DEFAULT 0,
          adjustment_events  INTEGER NOT NULL DEFAULT 0,
          rule_version       TEXT NOT NULL,
          source_kind        TEXT NOT NULL DEFAULT 'external_calibration',
          note               TEXT,
          updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (racer_number, period_start, period_end, rule_version, source_kind)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_kraw_unmatched (
          file_name       TEXT NOT NULL,
          line_number     INTEGER NOT NULL,
          race_date       TEXT NOT NULL,
          race_number     INTEGER,
          event_code      TEXT NOT NULL,
          boat_number     INTEGER NOT NULL,
          racer_number    INTEGER NOT NULL,
          reason          TEXT NOT NULL,
          detail_reason   TEXT,
          raw_line        TEXT NOT NULL,
          rule_version    TEXT NOT NULL,
          created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (file_name, line_number, rule_version)
        )
        """
    )
    ensure_column(conn, "racer_accident_kraw_unmatched", "detail_reason", "detail_reason TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_racer_accident_kraw_unmatched_date
          ON racer_accident_kraw_unmatched(race_date, reason)
        """
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO racer_accident_point_rules
          (rule_version, event_code, event_label, base_points, yusho_points,
           applies_from, applies_to, priority, source_kind, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(RULE_VERSION, *row) for row in DEFAULT_POINT_RULES],
    )


def load_point_rules(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT event_code, event_label, base_points, yusho_points,
               applies_from, applies_to, priority
          FROM racer_accident_point_rules
         WHERE rule_version = ?
         ORDER BY event_code, priority DESC, applies_from DESC
        """,
        (RULE_VERSION,),
    ).fetchall()
    rules: dict[str, dict] = {}
    for event_code, event_label, base_points, yusho_points, applies_from, applies_to, priority in rows:
        rules.setdefault(
            str(event_code),
            {
                "event_label": str(event_label),
                "base_points": int(base_points),
                "yusho_points": int(yusho_points) if yusho_points is not None else int(base_points),
                "applies_from": str(applies_from),
                "applies_to": str(applies_to) if applies_to else None,
                "priority": int(priority),
            },
        )
    return rules


def class_period(race_date: str) -> tuple[int, int, str, str]:
    """Return grading period key for a race date.

    BOATRACE class review windows are treated as May-Oct and Nov-Apr.  The
    period key follows the effective class term: races from May-Oct feed the
    next year's first-half term, and Nov-Apr feed the next year's second-half
    term.  This keeps historical rule comparisons explicit and adjustable.
    """
    d = date.fromisoformat(race_date)
    if 5 <= d.month <= 10:
        return d.year + 1, 1, f"{d.year}-05-01", f"{d.year}-10-31"
    if d.month >= 11:
        return d.year + 1, 2, f"{d.year}-11-01", f"{d.year + 1}-04-30"
    return d.year, 2, f"{d.year - 1}-11-01", f"{d.year}-04-30"


def affected_class_periods(date_from: str, date_to: str) -> list[tuple[int, int, str, str]]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    periods: list[tuple[int, int, str, str]] = []
    seen: set[tuple[int, int]] = set()
    probe = start
    while probe <= end:
        period = class_period(probe.isoformat())
        key = (period[0], period[1])
        if key not in seen:
            periods.append(period)
            seen.add(key)
        _, _, _period_start, period_end = period
        next_day = date.fromisoformat(period_end).toordinal() + 1
        probe = date.fromordinal(next_day)
    return periods


def normalize_remark(raw: str) -> str:
    return (raw or "").strip().upper().replace(" ", "")


def score_event(raw_remarks: str, is_yusho: int) -> Optional[tuple[str, str, int]]:
    """Map result remarks to accident points.

    Remarks vary by source and era.  We keep conservative labels and store the
    raw value so later official feeds can override or audit this mapping.
    """
    r = normalize_remark(raw_remarks)
    if not r:
        return None

    if r.startswith("F") or r.startswith("L"):
        points = 30 if is_yusho else 20
        return "FL", "F/L", points

    if "妨" in raw_remarks:
        return "OBSTRUCTION", "妨害失格", 15

    if r in {"K1", "S1", "S2"}:
        return r, "レーサー責任の失格・欠場", 10

    if r in {"K0", "S0"}:
        return r, "レーサー責任外の失格・欠場", 0

    if "不良" in raw_remarks or "待機" in raw_remarks:
        return "MINOR_VIOLATION", "不良航法・待機行動違反", 2

    if "失" in raw_remarks or "失格" in raw_remarks:
        return "DISQ_UNKNOWN", "失格/責任不明", 10

    return None


def score_event_with_rules(raw_remarks: str, is_yusho: int, rules: dict[str, dict]) -> Optional[tuple[str, str, int]]:
    direct_rule = rules.get(raw_remarks)
    if direct_rule is not None:
        points = int(direct_rule["yusho_points"] if is_yusho else direct_rule["base_points"])
        return str(raw_remarks), str(direct_rule["event_label"]), points
    scored = score_event(raw_remarks, is_yusho)
    if scored is None:
        return None
    event_code, fallback_label, fallback_points = scored
    rule = rules.get(event_code)
    if rule is None:
        return event_code, fallback_label, fallback_points
    points = int(rule["yusho_points"] if is_yusho else rule["base_points"])
    return event_code, str(rule["event_label"]), points


def iter_events(conn: sqlite3.Connection, date_from: str, date_to: str, rules: dict[str, dict]) -> Iterable[AccidentEvent]:
    sql = """
        SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
               e.racer_number, rr.boat_number, rr.course_number,
               e.class_number,
               COALESCE(r.is_yusho, 0) AS is_yusho,
               rr.remarks
          FROM race_results rr
          JOIN races r ON r.race_id = rr.race_id
          JOIN race_entries e
            ON e.race_id = rr.race_id
           AND e.boat_number = rr.boat_number
         WHERE r.race_date BETWEEN ? AND ?
           AND rr.remarks IS NOT NULL
           AND TRIM(rr.remarks) <> ''
    """
    for row in conn.execute(sql, (date_from, date_to)):
        (
            race_id,
            race_date,
            stadium_number,
            race_number,
            racer_number,
            boat_number,
            course_number,
            class_number,
            is_yusho,
            raw_remarks,
        ) = row
        scored = score_event_with_rules(str(raw_remarks), int(is_yusho or 0), rules)
        if scored is None:
            continue
        code, label, points = scored
        yield AccidentEvent(
            race_id=race_id,
            race_date=race_date,
            stadium_number=int(stadium_number),
            race_number=int(race_number),
            racer_number=int(racer_number),
            boat_number=int(boat_number),
            course_number=course_number,
            class_number=class_number,
            event_code=code,
            event_label=label,
            accident_points=int(points),
            is_yusho=int(is_yusho or 0),
            raw_remarks=str(raw_remarks),
        )


def k_file_date(path: Path) -> Optional[str]:
    stem = path.stem.upper()
    if len(stem) < 7 or not stem.startswith("K"):
        return None
    yymmdd = stem[1:7]
    if not yymmdd.isdigit():
        return None
    year = 2000 + int(yymmdd[:2])
    month = int(yymmdd[2:4])
    day = int(yymmdd[4:6])
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_race_number(line: str) -> Optional[int]:
    parts = line.strip().split()
    if not parts:
        return None
    head = parts[0].upper()
    if not head.endswith("R"):
        return None
    num = head[:-1]
    if not num.isdigit():
        return None
    race_number = int(num)
    if 1 <= race_number <= 12:
        return race_number
    return None


def parse_k_accident_line(line: str) -> Optional[tuple[str, int, int]]:
    parts = line.strip().split()
    if len(parts) < 3:
        return None
    code = parts[0].strip()
    if code not in RAW_ACCIDENT_CODES:
        return None
    if not parts[1].isdigit() or not parts[2].isdigit():
        return None
    boat_number = int(parts[1])
    racer_number = int(parts[2])
    if not (1 <= boat_number <= 6 and 1000 <= racer_number <= 9999):
        return None
    return code, boat_number, racer_number


def load_entry_lookup(conn: sqlite3.Connection, race_date: str) -> tuple[dict[tuple[int, int, int], list[tuple]], dict]:
    sql = """
        SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
               e.racer_number, e.boat_number, rr.course_number,
               e.class_number,
               COALESCE(r.is_yusho, 0) AS is_yusho
          FROM races r
          JOIN race_entries e ON e.race_id = r.race_id
         LEFT JOIN race_results rr
            ON rr.race_id = r.race_id
           AND rr.boat_number = e.boat_number
         WHERE r.race_date = ?
    """
    lookup: dict[tuple[int, int, int], list[tuple]] = {}
    context = {
        "entry_count": 0,
        "racers": set(),
        "race_boats": set(),
    }
    for row in conn.execute(sql, (race_date,)):
        race_number = int(row[3])
        boat_number = int(row[5])
        racer_number = int(row[4])
        key = (race_number, boat_number, racer_number)
        lookup.setdefault(key, []).append(row)
        context["entry_count"] += 1
        context["racers"].add(racer_number)
        context["race_boats"].add((race_number, boat_number))
    return lookup, context


def classify_no_match(context: dict, race_number: Optional[int], boat_number: int, racer_number: int) -> str:
    if race_number is None:
        return "race_number_not_found"
    if context["entry_count"] == 0:
        return "no_entries_for_date"
    if racer_number not in context["racers"]:
        return "racer_not_in_entries_on_date"
    if (race_number, boat_number) not in context["race_boats"]:
        return "race_boat_not_in_entries_on_date"
    return "racer_exists_but_not_same_race_boat"


def iter_k_raw_events(
    conn: sqlite3.Connection,
    date_from: str,
    date_to: str,
    rules: dict[str, dict],
) -> tuple[list[AccidentEvent], list[KRawUnmatched], dict[str, int]]:
    raw_dir = Path(config.OFFICIAL_RESULTS_DIR)
    stats = {
        "raw_rows": 0,
        "matched": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "files": 0,
        "dates_with_files": set(),
    }
    events: list[AccidentEvent] = []
    unmatched_rows: list[KRawUnmatched] = []
    raw_paths = {p.resolve() for p in raw_dir.rglob("*.TXT")}
    raw_paths.update(p.resolve() for p in raw_dir.rglob("*.txt"))
    for path in sorted(raw_paths):
        race_date = k_file_date(path)
        if race_date is None or race_date < date_from or race_date > date_to:
            continue
        stats["files"] += 1
        stats["dates_with_files"].add(race_date)
        lookup, context = load_entry_lookup(conn, race_date)
        race_number: Optional[int] = None
        try:
            lines = path.read_text("cp932", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, 1):
            maybe_race = parse_race_number(line)
            if maybe_race is not None:
                race_number = maybe_race
                continue
            parsed = parse_k_accident_line(line)
            if parsed is None:
                continue
            stats["raw_rows"] += 1
            if race_number is None:
                stats["unmatched"] += 1
                unmatched_rows.append(
                    KRawUnmatched(
                        file_name=path.name,
                        line_number=line_number,
                        race_date=race_date,
                        race_number=None,
                        event_code=parsed[0],
                        boat_number=parsed[1],
                        racer_number=parsed[2],
                        reason="race_number_not_found",
                        detail_reason="race_number_not_found",
                        raw_line=line.strip(),
                    )
                )
                continue
            code, boat_number, racer_number = parsed
            matches = lookup.get((race_number, boat_number, racer_number), [])
            if not matches:
                stats["unmatched"] += 1
                detail_reason = classify_no_match(context, race_number, boat_number, racer_number)
                unmatched_rows.append(
                    KRawUnmatched(
                        file_name=path.name,
                        line_number=line_number,
                        race_date=race_date,
                        race_number=race_number,
                        event_code=code,
                        boat_number=boat_number,
                        racer_number=racer_number,
                        reason="no_matching_entry",
                        detail_reason=detail_reason,
                        raw_line=line.strip(),
                    )
                )
                continue
            if len(matches) > 1:
                stats["ambiguous"] += 1
                unmatched_rows.append(
                    KRawUnmatched(
                        file_name=path.name,
                        line_number=line_number,
                        race_date=race_date,
                        race_number=race_number,
                        event_code=code,
                        boat_number=boat_number,
                        racer_number=racer_number,
                        reason="ambiguous_entry",
                        detail_reason="ambiguous_entry",
                        raw_line=line.strip(),
                    )
                )
                continue
            (
                race_id,
                race_date,
                stadium_number,
                race_number,
                racer_number,
                boat_number,
                course_number,
                class_number,
                is_yusho,
            ) = matches[0]
            scored = score_event_with_rules(code, int(is_yusho or 0), rules)
            if scored is None:
                stats["unmatched"] += 1
                unmatched_rows.append(
                    KRawUnmatched(
                        file_name=path.name,
                        line_number=line_number,
                        race_date=str(race_date),
                        race_number=int(race_number),
                        event_code=code,
                        boat_number=int(boat_number),
                        racer_number=int(racer_number),
                        reason="unscored_code",
                        detail_reason="unscored_code",
                        raw_line=line.strip(),
                    )
                )
                continue
            event_code, label, points = scored
            stats["matched"] += 1
            events.append(
                AccidentEvent(
                    race_id=str(race_id),
                    race_date=str(race_date),
                    stadium_number=int(stadium_number),
                    race_number=int(race_number),
                    racer_number=int(racer_number),
                    boat_number=int(boat_number),
                    course_number=course_number,
                    class_number=class_number,
                    event_code=event_code,
                    event_label=label,
                    accident_points=int(points),
                    is_yusho=int(is_yusho or 0),
                    raw_remarks=f"KRAW:{line.strip()}",
                )
            )
    return events, unmatched_rows, stats


def load_existing_kraw_events(
    conn: sqlite3.Connection,
    date_from: str,
    date_to: str,
    preserve_dates: set[str],
) -> list[AccidentEvent]:
    if not preserve_dates:
        return []
    placeholders = ",".join("?" for _ in preserve_dates)
    sql = f"""
        SELECT race_id, race_date, stadium_number, race_number, racer_number,
               boat_number, course_number, class_number, event_code, event_label,
               accident_points, is_yusho, raw_remarks, rule_version
          FROM racer_accident_events
         WHERE race_date BETWEEN ? AND ?
           AND rule_version = ?
           AND substr(raw_remarks, 1, 5) = 'KRAW:'
           AND race_date IN ({placeholders})
    """
    params: list[object] = [date_from, date_to, RULE_VERSION, *sorted(preserve_dates)]
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        AccidentEvent(
            race_id=str(row[0]),
            race_date=str(row[1]),
            stadium_number=int(row[2]),
            race_number=int(row[3]),
            racer_number=int(row[4]),
            boat_number=int(row[5]),
            course_number=row[6],
            class_number=row[7],
            event_code=str(row[8]),
            event_label=str(row[9]),
            accident_points=int(row[10]),
            is_yusho=int(row[11] or 0),
            raw_remarks=str(row[12] or ""),
            rule_version=str(row[13] or RULE_VERSION),
        )
        for row in rows
    ]


def dedupe_events(events: Iterable[AccidentEvent]) -> list[AccidentEvent]:
    deduped: dict[tuple[str, int, str], AccidentEvent] = {}
    for ev in events:
        key = (ev.race_id, ev.racer_number, ev.event_code)
        current = deduped.get(key)
        if current is None or ev.raw_remarks.startswith("KRAW:"):
            deduped[key] = ev
    return list(deduped.values())


def write_unmatched_csv(path: Path, rows: Iterable[KRawUnmatched]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "file_name",
        "line_number",
        "race_date",
        "race_number",
        "event_code",
        "boat_number",
        "racer_number",
        "reason",
        "detail_reason",
        "raw_line",
        "rule_version",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: getattr(row, name) for name in fieldnames})


def rebuild(conn: sqlite3.Connection, date_from: str, date_to: str, dry_run: bool = False) -> dict:
    ensure_tables(conn)
    rules = load_point_rules(conn)
    db_events = list(iter_events(conn, date_from, date_to, rules))
    raw_events, unmatched_rows, raw_stats = iter_k_raw_events(conn, date_from, date_to, rules)
    raw_dates = {str(d) for d in raw_stats.get("dates_with_files", set())}
    existing_dates = set()
    preserved_existing_events: list[AccidentEvent] = []
    if not dry_run:
        existing_rows = conn.execute(
            """
            SELECT DISTINCT race_date
              FROM racer_accident_events
             WHERE race_date BETWEEN ? AND ?
               AND rule_version = ?
               AND substr(raw_remarks, 1, 5) = 'KRAW:'
            """,
            (date_from, date_to, RULE_VERSION),
        ).fetchall()
        existing_dates = {str(row[0]) for row in existing_rows if row and row[0]}
        preserve_dates = existing_dates - raw_dates
        preserved_existing_events = load_existing_kraw_events(conn, date_from, date_to, preserve_dates)
    events = dedupe_events([*db_events, *raw_events, *preserved_existing_events])

    if not dry_run:
        conn.execute("DELETE FROM racer_accident_events WHERE race_date BETWEEN ? AND ? AND rule_version = ?", (date_from, date_to, RULE_VERSION))
        conn.execute(
            "DELETE FROM racer_accident_kraw_unmatched WHERE race_date BETWEEN ? AND ? AND rule_version = ?",
            (date_from, date_to, RULE_VERSION),
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO racer_accident_events
              (race_id, racer_number, boat_number, race_date, stadium_number,
               race_number, course_number, class_number, event_code, event_label,
               accident_points, is_yusho, raw_remarks, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ev.race_id,
                    ev.racer_number,
                    ev.boat_number,
                    ev.race_date,
                    ev.stadium_number,
                    ev.race_number,
                    ev.course_number,
                    ev.class_number,
                    ev.event_code,
                    ev.event_label,
                    ev.accident_points,
                    ev.is_yusho,
                    ev.raw_remarks,
                    ev.rule_version,
                )
                for ev in events
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO racer_accident_kraw_unmatched
              (file_name, line_number, race_date, race_number, event_code,
               boat_number, racer_number, reason, detail_reason, raw_line, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.file_name,
                    row.line_number,
                    row.race_date,
                    row.race_number,
                    row.event_code,
                    row.boat_number,
                    row.racer_number,
                    row.reason,
                    row.detail_reason,
                    row.raw_line,
                    row.rule_version,
                )
                for row in unmatched_rows
            ],
        )

        # Period stats must be refreshed even on days with no accident events:
        # starts_count still changes, and accident_rate is points / starts.
        periods = affected_class_periods(date_from, date_to)
        for period_year, period_half, period_start, period_end in periods:
            effective_period_start = period_start
            effective_period_end = min(period_end, date_to)
            conn.execute(
                """
                DELETE FROM racer_accident_period_stats
                 WHERE period_year = ?
                   AND period_half = ?
                   AND period_end = ?
                   AND rule_version = ?
                   AND source_kind = 'internal_rebuild'
                """,
                (period_year, period_half, effective_period_end, RULE_VERSION),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO racer_accident_period_stats
                  (racer_number, period_year, period_half, period_start, period_end,
                   starts_count, accident_events, accident_points, accident_rate,
                   rule_version, source_kind, updated_at)
                WITH starts_base AS (
                    SELECT e.race_id, e.racer_number
                      FROM race_entries e
                      JOIN races r ON r.race_id = e.race_id
                      JOIN race_results rr
                        ON rr.race_id = e.race_id
                       AND rr.boat_number = e.boat_number
                     WHERE r.race_date BETWEEN ? AND ?
                       AND rr.start_timing IS NOT NULL
                       AND rr.finishing_position IS NOT NULL
                    UNION
                    SELECT race_id, racer_number
                      FROM racer_accident_events
                     WHERE race_date BETWEEN ? AND ?
                       AND event_code = 'FL'
                       AND rule_version = ?
                ),
                starts AS (
                    SELECT racer_number, COUNT(*) AS starts_count
                      FROM starts_base
                     GROUP BY racer_number
                ),
                fl_ranked AS (
                    SELECT racer_number,
                           is_yusho,
                           ROW_NUMBER() OVER (
                             PARTITION BY racer_number
                             ORDER BY race_date, race_id
                           ) AS fl_seq
                      FROM racer_accident_events
                     WHERE race_date BETWEEN ? AND ?
                       AND rule_version = ?
                       AND event_code = 'FL'
                ),
                fl_penalty AS (
                    SELECT racer_number,
                           SUM(
                             CASE
                               WHEN fl_seq <= 1 THEN 0
                               WHEN is_yusho = 1 THEN 20
                               ELSE 10
                             END
                           ) AS repeat_fl_points
                      FROM fl_ranked
                     GROUP BY racer_number
                ),
                acc AS (
                    SELECT racer_number,
                           COUNT(*) AS accident_events,
                           SUM(accident_points) AS accident_points
                      FROM racer_accident_events
                     WHERE race_date BETWEEN ? AND ?
                       AND rule_version = ?
                     GROUP BY racer_number
                ),
                adj AS (
                    SELECT racer_number,
                           SUM(adjustment_points) AS adjustment_points,
                           SUM(adjustment_events) AS adjustment_events
                      FROM racer_accident_period_adjustments
                     WHERE period_start = ?
                       AND period_end = ?
                       AND rule_version = ?
                     GROUP BY racer_number
                )
                SELECT s.racer_number, ?, ?, ?, ?,
                       s.starts_count,
                       COALESCE(acc.accident_events, 0) + COALESCE(adj.adjustment_events, 0),
                       COALESCE(acc.accident_points, 0) + COALESCE(fl_penalty.repeat_fl_points, 0) + COALESCE(adj.adjustment_points, 0),
                       CASE WHEN s.starts_count > 0
                            THEN CAST(COALESCE(acc.accident_points, 0) + COALESCE(fl_penalty.repeat_fl_points, 0) + COALESCE(adj.adjustment_points, 0) AS REAL) / s.starts_count
                            ELSE NULL END,
                       ?, 'internal_rebuild', CURRENT_TIMESTAMP
                  FROM starts s
                  LEFT JOIN acc ON acc.racer_number = s.racer_number
                  LEFT JOIN fl_penalty ON fl_penalty.racer_number = s.racer_number
                  LEFT JOIN adj ON adj.racer_number = s.racer_number
                """,
                (
                    effective_period_start,
                    effective_period_end,
                    effective_period_start,
                    effective_period_end,
                    RULE_VERSION,
                    effective_period_start,
                    effective_period_end,
                    RULE_VERSION,
                    effective_period_start,
                    effective_period_end,
                    RULE_VERSION,
                    effective_period_start,
                    effective_period_end,
                    RULE_VERSION,
                    period_year,
                    period_half,
                    effective_period_start,
                    effective_period_end,
                    RULE_VERSION,
                ),
            )
        conn.commit()

    by_code: dict[str, int] = {}
    by_points: dict[str, int] = {}
    unmatched_by_reason: dict[str, int] = {}
    unmatched_by_detail_reason: dict[str, int] = {}
    for ev in events:
        by_code[ev.event_code] = by_code.get(ev.event_code, 0) + 1
        by_points[ev.event_code] = by_points.get(ev.event_code, 0) + ev.accident_points
    for row in unmatched_rows:
        unmatched_by_reason[row.reason] = unmatched_by_reason.get(row.reason, 0) + 1
        unmatched_by_detail_reason[row.detail_reason] = unmatched_by_detail_reason.get(row.detail_reason, 0) + 1
    return {
        "date_from": date_from,
        "date_to": date_to,
        "events": len(events),
        "db_events": len(db_events),
        "raw_events": len(raw_events),
        "preserved_existing_events": len(preserved_existing_events),
        "raw_dates": sorted(raw_dates),
        "existing_kraw_dates": sorted(existing_dates),
        "unmatched_rows": unmatched_rows,
        "unmatched_by_reason": unmatched_by_reason,
        "unmatched_by_detail_reason": unmatched_by_detail_reason,
        "raw_stats": raw_stats,
        "by_code": by_code,
        "by_points": by_points,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", default="2016-01-01")
    parser.add_argument("--to", dest="date_to", default=_today_jst().isoformat())
    parser.add_argument("--local", action="store_true", help="Force local SQLite. This avoids production writes.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--export-unmatched",
        default=str(ROOT_DIR / "reports" / "kraw_unmatched_accident_rows.csv"),
        help="CSV path for K raw rows that could not be matched to DB entries.",
    )
    args = parser.parse_args()

    if not args.local and not args.dry_run:
        assert_safe_production_write(
            action="rebuild_racer_accident_stats",
            allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
        )

    conn = db_connect(config.DB_PATH) if args.local else db_connect()
    try:
        stats = rebuild(conn, args.date_from, args.date_to, dry_run=args.dry_run)
    finally:
        conn.close()

    print(f"range={stats['date_from']}..{stats['date_to']} dry_run={stats['dry_run']}")
    print(
        "events="
        f"{stats['events']} db_events={stats['db_events']} raw_events={stats['raw_events']} "
        f"preserved_existing={stats['preserved_existing_events']}"
    )
    print(
        "k_raw "
        f"files={stats['raw_stats']['files']} rows={stats['raw_stats']['raw_rows']} "
        f"matched={stats['raw_stats']['matched']} unmatched={stats['raw_stats']['unmatched']} "
        f"ambiguous={stats['raw_stats']['ambiguous']}"
    )
    if stats["unmatched_by_reason"]:
        print("unmatched_by_reason")
        for reason in sorted(stats["unmatched_by_reason"]):
            print(f"  {reason}: {stats['unmatched_by_reason'][reason]}")
    if stats["unmatched_by_detail_reason"]:
        print("unmatched_by_detail_reason")
        for reason in sorted(stats["unmatched_by_detail_reason"]):
            print(f"  {reason}: {stats['unmatched_by_detail_reason'][reason]}")
    if args.export_unmatched:
        export_path = Path(args.export_unmatched)
        write_unmatched_csv(export_path, stats["unmatched_rows"])
        print(f"unmatched_csv={export_path}")
    for code in sorted(stats["by_code"]):
        print(f"  {code}: n={stats['by_code'][code]} points={stats['by_points'][code]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
