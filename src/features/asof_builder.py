"""Build leakage-safe, one-row-per-race feature snapshots.

The source database is always treated as read-only by this module.  Callers are
responsible for opening ``data/boatrace.db`` with
``src.db.connection.connect()``; this module writes only to the separate
``kachisuji_search.db`` SQLite file.

Timing groups:

* program fields and the one-year racer aggregates are available by the day
  before a race (``asof_date``);
* preview weather and exhibition fields are same-day observations and are
  retained for historical filtering, but are not pre-race-day facts.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import permutations, product
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
from typing import Any, Iterable, Mapping, Sequence

from src.features.accident_history import (
    RestoredAccidentHistory,
    RestoredStartTimingHistory,
    load_restored_histories,
    load_start_timing_histories,
)


SCHEMA_VERSION = 7
SQLITE_VARIABLE_CHUNK_SIZE = 900
BOATS = range(1, 7)
KIMARITE_KEYS = ("nige", "sashi", "makuri", "makurizashi", "nuki", "megumare")
KIMARITE_LABELS = {
    "逃げ": "nige",
    "差し": "sashi",
    "まくり": "makuri",
    "まくり差し": "makurizashi",
    "抜き": "nuki",
    "恵まれ": "megumare",
}
CLASS_LABELS = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}
ORIENTATION_MASTER_PATH = Path(__file__).resolve().parents[2] / "master" / "stadium_orientations.json"

# Codes accepted by the official K parser when a numeric finishing position is
# unavailable.  The production SQLite snapshot inspected on 2026-08-15
# contains S0/S1/S2; the wider parser-supported set keeps the definition stable
# if F/L, disqualification, capsize, fall, or interference appears later.
ACCIDENT_REMARK_CODES = frozenset(
    {"K0", "K1", "S0", "S1", "S2", "F", "L", "失", "失格", "転", "落", "妨"}
)


def _boat_columns() -> list[tuple[str, str]]:
    columns: list[tuple[str, str]] = []
    for boat in BOATS:
        columns.extend(
            [
                (f"b{boat}_racer_id", "INTEGER"),
                (f"b{boat}_class", "TEXT"),
                (f"b{boat}_age", "INTEGER"),
                (f"b{boat}_avg_st", "REAL"),
                (f"b{boat}_avg_st_n", "INTEGER NOT NULL DEFAULT 0"),
                (f"b{boat}_avg_st_official", "REAL"),
                (f"b{boat}_national_rate", "REAL"),
                (f"b{boat}_local_rate", "REAL"),
                (f"b{boat}_national_rate2", "REAL"),
                (f"b{boat}_local_rate2", "REAL"),
                (f"b{boat}_motor_rate2", "REAL"),
                (f"b{boat}_ex_time", "REAL"),
                (f"b{boat}_ex_rank", "INTEGER"),
                (f"b{boat}_ex_dev", "REAL"),
                (f"b{boat}_ex_st", "REAL"),
            ]
        )
        columns.extend((f"b{boat}_kimarite_rate_{key}", "REAL") for key in KIMARITE_KEYS)
        columns.extend(
            [
                (f"b{boat}_accident_rate", "REAL"),
                (f"b{boat}_accident_rate_365d", "REAL"),
                (f"b{boat}_accident_points", "REAL"),
                (f"b{boat}_accident_source", "TEXT"),
                (f"b{boat}_accident_rate_period", "REAL"),
                (f"b{boat}_accident_count_period", "INTEGER"),
                (f"b{boat}_starts_period", "INTEGER"),
            ]
        )
    return columns


BASE_COLUMNS: list[tuple[str, str]] = [
    ("race_id", "TEXT PRIMARY KEY"),
    ("race_date", "TEXT NOT NULL"),
    ("asof_date", "TEXT NOT NULL"),
    ("built_at", "TEXT NOT NULL"),
    ("schema_version", "INTEGER NOT NULL"),
    ("jcd", "INTEGER"),
    ("race_no", "INTEGER"),
    ("grade", "INTEGER"),
    ("day_index", "TEXT"),
    ("daypart", "TEXT"),
    ("female_present", "INTEGER"),
    ("class_mix", "TEXT"),
    ("tide_phase", "TEXT"),
    ("weather", "TEXT"),
    ("wind_dir", "TEXT"),
    ("wind_dir_raw", "INTEGER"),
    ("wind_speed", "REAL"),
    ("t5_odds_favorite", "REAL"),
]
RESULT_COLUMNS: list[tuple[str, str]] = [
    ("result_sanrentan", "TEXT"),
    ("payout_sanrentan", "INTEGER"),
    ("result_sanrentan_json", "TEXT"),
    ("payout_sanrentan_json", "TEXT"),
    ("result_nirentan", "TEXT"),
    ("payout_nirentan", "INTEGER"),
    ("result_nirentan_json", "TEXT"),
    ("payout_nirentan_json", "TEXT"),
    ("result_tansho", "INTEGER"),
    ("payout_tansho", "INTEGER"),
    ("result_tansho_json", "TEXT"),
    ("payout_tansho_json", "TEXT"),
]
ALL_COLUMNS = BASE_COLUMNS + _boat_columns() + RESULT_COLUMNS


def create_output_schema(conn: sqlite3.Connection) -> None:
    """Create or additively migrate the Step 1 output table and date index.

    Existing rows retain their original schema version and receive NULL for
    newly added columns; only later builds write the current version.
    """

    ddl = ",\n  ".join(f"{name} {kind}" for name, kind in ALL_COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS asof_race_features (\n  {ddl}\n)")
    existing = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(asof_race_features)")
    }
    for name, kind in ALL_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE asof_race_features ADD COLUMN {name} {kind}")
    if "b1_accident_rate_365d" not in existing:
        # Schema <=4 stored the 365-day remark rate under bN_accident_rate.
        # Move it before schema v5 reuses that name for the legacy-ROI period
        # rate.  The update is idempotent and preserves the historical value.
        for boat in BOATS:
            conn.execute(
                f"UPDATE asof_race_features "
                f"SET b{boat}_accident_rate_365d=b{boat}_accident_rate, "
                f"b{boat}_accident_rate=NULL WHERE schema_version < 5"
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_asof_race_features_date "
        "ON asof_race_features(race_date)"
    )
    conn.commit()


def open_output(path: str | Path) -> sqlite3.Connection:
    """Open the separate feature database; never use this for boatrace.db."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_path)
    conn.row_factory = sqlite3.Row
    create_output_schema(conn)
    return conn


def _rows(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(params))
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _rows_for_ids(
    conn: Any,
    sql_template: str,
    ids: Sequence[Any],
    params: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    """Run an ``IN`` query in batches below SQLite's variable limit."""

    result: list[dict[str, Any]] = []
    for offset in range(0, len(ids), SQLITE_VARIABLE_CHUNK_SIZE):
        chunk = ids[offset : offset + SQLITE_VARIABLE_CHUNK_SIZE]
        placeholders = ",".join("?" for _ in chunk)
        result.extend(
            _rows(
                conn,
                sql_template.format(placeholders=placeholders),
                [*params, *chunk],
            )
        )
    return result


def _one(conn: Any, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    result = _rows(conn, sql, params)
    return result[0] if result else None


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _age_on_date(birth_date: Any, race_date: str) -> int | None:
    """Return full years of age on ``race_date`` from an ISO birth date."""

    if birth_date is None:
        return None
    try:
        born = date.fromisoformat(str(birth_date)[:10])
        raced = date.fromisoformat(race_date)
    except (TypeError, ValueError):
        return None
    if born > raced:
        return None
    return raced.year - born.year - ((raced.month, raced.day) < (born.month, born.day))


def weather_label(weather_number: Any) -> str | None:
    """Map observed weather: 1=晴, 2=曇, 3=雨, other codes=その他."""

    if weather_number is None:
        return None
    try:
        code = int(weather_number)
    except (TypeError, ValueError):
        return None
    return {1: "晴", 2: "曇", 3: "雨"}.get(code, "その他")


def classify_daypart(first_race_closed_at: Any) -> str | None:
    """Classify by the venue-day first-race scheduled close time.

    The source has no separate start timestamp, so ``race_closed_at`` (the
    published telephone-vote deadline) is the schedule proxy.  Before 10:30 is
    モーニング, 14:00 or later is ナイター, and the interval is デイ.
    """

    if not first_race_closed_at:
        return None
    text = str(first_race_closed_at)
    try:
        clock = datetime.fromisoformat(text).time()
    except ValueError:
        try:
            clock = datetime.strptime(text[-5:], "%H:%M").time()
        except ValueError:
            return None
    minutes = clock.hour * 60 + clock.minute
    if minutes < 10 * 60 + 30:
        return "モーニング"
    if minutes >= 14 * 60:
        return "ナイター"
    return "デイ"


def normalize_tide_phase(row: dict[str, Any] | None) -> str | None:
    """Use the stored scheduled-race tide classification.

    ``race_tides`` uses a ±90 minute high/low zone and otherwise records a
    rising/falling phase.  Unknown phases remain NULL.
    """

    if not row:
        return None
    phase = str(row.get("tide_phase") or "").lower()
    return {
        "high": "満潮前後",
        "low": "干潮前後",
        "rising": "上げ潮",
        "falling": "下げ潮",
    }.get(phase)


def exhibition_metrics(times: dict[int, float | None]) -> dict[int, tuple[int | None, float | None]]:
    """Return competition rank and deviation from the six-boat mean.

    Rank is ``1 + count(times strictly faster)`` so ties share a rank.  Both
    derived values stay NULL unless all six finite exhibition times exist.
    """

    clean = {boat: _finite_float(times.get(boat)) for boat in BOATS}
    if any(clean[boat] is None for boat in BOATS):
        return {boat: (None, None) for boat in BOATS}
    values = [clean[boat] for boat in BOATS]
    mean = sum(values) / 6  # type: ignore[arg-type]
    return {
        boat: (
            1 + sum(value < clean[boat] for value in values),  # type: ignore[operator]
            clean[boat] - mean,  # type: ignore[operator]
        )
        for boat in BOATS
    }


@dataclass(frozen=True)
class RacerHistory:
    dates: tuple[str, ...]
    starts_prefix: tuple[int, ...]
    accident_prefix: tuple[int, ...]
    kimarite_prefix: dict[str, tuple[int, ...]]

    def rates(self, window_start: str, asof_date: str) -> dict[str, float | None]:
        left = bisect_left(self.dates, window_start)
        right = bisect_right(self.dates, asof_date)
        starts = self.starts_prefix[right] - self.starts_prefix[left]
        if starts == 0:
            return {**{key: None for key in KIMARITE_KEYS}, "accident": None}
        output = {
            key: (self.kimarite_prefix[key][right] - self.kimarite_prefix[key][left])
            * 100.0
            / starts
            for key in KIMARITE_KEYS
        }
        output["accident"] = (
            self.accident_prefix[right] - self.accident_prefix[left]
        ) * 100.0 / starts
        return output


def _load_histories(
    source: Any,
    window_start: str,
    window_end: str,
    racer_ids: Iterable[int] | None = None,
) -> dict[int, RacerHistory]:
    """Load guarded racer history through ``window_end``.

    A kimarite is authoritative only on a row whose finishing position is 1.
    An accident remark is counted only when the position is not a numeric
    official finish from 1 through 6.  This rejects legacy rows that combine
    an accident code with an ordinary numeric finish.
    """

    params: list[Any] = [window_start, window_end]
    ids = sorted({int(value) for value in racer_ids or []})
    sql = """SELECT e.racer_number, r.race_date, rr.finishing_position,
                    rr.kimarite, rr.remarks
             FROM races r
             JOIN race_entries e ON e.race_id = r.race_id
             JOIN race_results rr
               ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
            WHERE r.race_date BETWEEN ? AND ?{racer_filter}
            ORDER BY e.racer_number, r.race_date, r.race_id"""
    if ids:
        rows = _rows_for_ids(
            source,
            sql.format(racer_filter=" AND e.racer_number IN ({placeholders})"),
            ids,
            params,
        )
    else:
        rows = _rows(source, sql.format(racer_filter=""), params)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["racer_number"] is not None:
            grouped[int(row["racer_number"])].append(row)
    histories: dict[int, RacerHistory] = {}
    for racer_id, items in grouped.items():
        dates: list[str] = []
        starts = [0]
        accidents = [0]
        kimarite = {key: [0] for key in KIMARITE_KEYS}
        for item in items:
            dates.append(_iso(item["race_date"]))
            starts.append(starts[-1] + 1)
            remark = str(item.get("remarks") or "").strip()
            try:
                finishing_position = int(item.get("finishing_position"))
            except (TypeError, ValueError):
                finishing_position = None
            valid_numeric_finish = finishing_position in BOATS
            accidents.append(
                accidents[-1]
                + int(remark in ACCIDENT_REMARK_CODES and not valid_numeric_finish)
            )
            winning_key = (
                KIMARITE_LABELS.get(str(item.get("kimarite") or "").strip())
                if finishing_position == 1
                else None
            )
            for key in KIMARITE_KEYS:
                kimarite[key].append(kimarite[key][-1] + int(winning_key == key))
        histories[racer_id] = RacerHistory(
            tuple(dates),
            tuple(starts),
            tuple(accidents),
            {key: tuple(values) for key, values in kimarite.items()},
        )
    return histories


def _history_rates(
    histories: dict[int, RacerHistory], racer_id: Any, asof_date: str
) -> dict[str, float | None]:
    if racer_id is None or int(racer_id) not in histories:
        return {**{key: None for key in KIMARITE_KEYS}, "accident": None}
    window_start = (date.fromisoformat(asof_date) - timedelta(days=364)).isoformat()
    return histories[int(racer_id)].rates(window_start, asof_date)


ACCIDENT_SOURCE_KIND = "reconstructed"
ACCIDENT_RULE_VERSION = "official_table_2025_05_reconstructed_v2"


def _accident_period_start_for_date(race_date: str) -> str:
    """Return the legacy ROI assessment-period start for a race date."""

    value = date.fromisoformat(race_date)
    if 5 <= value.month <= 10:
        return f"{value.year:04d}-05-01"
    if value.month >= 11:
        return f"{value.year:04d}-11-01"
    return f"{value.year - 1:04d}-11-01"


def _source_has_table(source: Any, table: str) -> bool:
    try:
        return source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    except Exception:
        return False


def _load_period_accidents(
    source: Any,
    races: Sequence[dict[str, Any]],
    racer_ids: set[int],
) -> dict[str, Any]:
    """Load the exact race-safe accident snapshots used by legacy ROI.

    The effective snapshot is the maximum ``period_end`` across the whole
    assessment period with ``period_end < race_date`` after filtering to the
    production ``source_kind`` and ``rule_version``.  It is deliberately not
    the latest row per racer: if the selected global snapshot has no row for a
    racer, legacy ROI's LEFT JOIN plus COALESCE treats that racer as zero.
    """

    if not races or not _source_has_table(source, "racer_accident_period_stats"):
        return {"ends": {}, "rows": {}}
    period_starts = sorted(
        {_accident_period_start_for_date(_iso(race["race_date"])) for race in races}
    )
    placeholders = ",".join("?" for _ in period_starts)
    latest_race_date = max(_iso(race["race_date"]) for race in races)
    end_rows = _rows(
        source,
        f"""SELECT DISTINCT period_start,period_end
              FROM racer_accident_period_stats
             WHERE period_start IN ({placeholders})
               AND source_kind=? AND rule_version=? AND period_end < ?
             ORDER BY period_start,period_end""",
        (*period_starts, ACCIDENT_SOURCE_KIND, ACCIDENT_RULE_VERSION, latest_race_date),
    )
    ends: dict[str, list[str]] = defaultdict(list)
    for item in end_rows:
        if item.get("period_start") and item.get("period_end"):
            ends[str(item["period_start"])].append(_iso(item["period_end"]))

    values: dict[tuple[str, str, int], tuple[float, float]] = {}
    if racer_ids:
        for offset in range(0, len(racer_ids), SQLITE_VARIABLE_CHUNK_SIZE):
            racer_chunk = sorted(racer_ids)[offset : offset + SQLITE_VARIABLE_CHUNK_SIZE]
            racer_placeholders = ",".join("?" for _ in racer_chunk)
            rows = _rows(
                source,
                f"""SELECT racer_number,period_start,period_end,
                            accident_rate,accident_points
                       FROM racer_accident_period_stats
                      WHERE period_start IN ({placeholders})
                        AND racer_number IN ({racer_placeholders})
                        AND source_kind=? AND rule_version=?""",
                (
                    *period_starts,
                    *racer_chunk,
                    ACCIDENT_SOURCE_KIND,
                    ACCIDENT_RULE_VERSION,
                ),
            )
            for item in rows:
                key = (
                    str(item["period_start"]),
                    _iso(item["period_end"]),
                    int(item["racer_number"]),
                )
                values[key] = (
                    float(item.get("accident_rate") or 0.0),
                    float(item.get("accident_points") or 0.0),
                )
    return {"ends": dict(ends), "rows": values}


def _period_accident_values(
    loaded: Mapping[str, Any], race_date: str, racer_id: Any
) -> tuple[float, float, str]:
    """Return legacy-compatible rate/points plus a missingness provenance."""

    period_start = _accident_period_start_for_date(race_date)
    candidates = loaded.get("ends", {}).get(period_start, [])
    index = bisect_left(candidates, race_date) - 1
    if index < 0 or racer_id is None:
        return 0.0, 0.0, "missing_zero"
    key = (period_start, candidates[index], int(racer_id))
    value = loaded.get("rows", {}).get(key)
    if value is None:
        return 0.0, 0.0, "missing_zero"
    return float(value[0]), float(value[1]), "period"


def _restored_period_accident_values(
    histories: Mapping[int, RestoredAccidentHistory],
    race_date: str,
    racer_id: Any,
) -> tuple[float | None, int, int]:
    """Return restored counts in ``[assessment start, race_date)``.

    The exclusive upper bound is the Step 13 as-of guarantee: an accident or
    start on the target race date, and every later event, is ineligible.
    """

    if racer_id is None:
        return None, 0, 0
    history = histories.get(int(racer_id))
    if history is None:
        return None, 0, 0
    return history.period_values(
        _accident_period_start_for_date(race_date), race_date
    )


def _restored_average_start_timing_values(
    histories: Mapping[int, RestoredStartTimingHistory],
    race_date: str,
    racer_id: Any,
) -> tuple[float | None, int]:
    """Return ordinary-ST mean/count for the 180 dates before a race.

    For race date ``D``, the exact window is ``[D - 180 days, D)``: the 180
    calendar dates ending on the previous day.  The exclusive upper bound
    prevents same-day and future leakage.  F, L, and NULL events were excluded
    when histories were loaded; zero valid starts therefore returns
    ``(NULL, 0)`` rather than a fabricated zero average.
    """

    if racer_id is None:
        return None, 0
    history = histories.get(int(racer_id))
    if history is None:
        return None, 0
    period_start = (date.fromisoformat(race_date) - timedelta(days=180)).isoformat()
    return history.average(period_start, race_date)


def _load_t5_favorite_odds(
    source: Any, race_ids: Sequence[str]
) -> dict[str, float]:
    """Return each race's minimum positive odds from its latest T-5 rows."""

    if not race_ids or not _source_has_table(source, "odds_trifecta"):
        return {}
    rows = _rows_for_ids(
        source,
        """WITH latest AS (
               SELECT race_id,combination,MAX(recorded_at) AS recorded_at
                 FROM odds_trifecta
                WHERE snapshot_label='T-5min' AND race_id IN ({placeholders})
                GROUP BY race_id,combination
             )
             SELECT odds.race_id,MIN(odds.odds) AS favorite_odds
               FROM odds_trifecta AS odds
               JOIN latest
                 ON latest.race_id=odds.race_id
                AND latest.combination=odds.combination
                AND latest.recorded_at=odds.recorded_at
              WHERE odds.snapshot_label='T-5min' AND odds.odds > 0
              GROUP BY odds.race_id""",
        race_ids,
    )
    return {
        str(item["race_id"]): float(item["favorite_odds"])
        for item in rows
        if item.get("favorite_odds") is not None
    }


def load_stadium_orientations(
    path: str | Path = ORIENTATION_MASTER_PATH,
) -> dict[int, float | None]:
    """Load and validate all 24 home-stretch course headings."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    venues = payload.get("venues")
    if not isinstance(venues, dict) or set(venues) != {str(i) for i in range(1, 25)}:
        raise ValueError("stadium orientation master must define venues 1 through 24")
    output: dict[int, float | None] = {}
    for venue in range(1, 25):
        item = venues[str(venue)]
        if not isinstance(item, dict) or "home_stretch_heading_deg" not in item:
            raise ValueError(f"stadium orientation venue {venue} is invalid")
        raw = item["home_stretch_heading_deg"]
        if raw is None:
            output[venue] = None
            continue
        heading = _finite_float(raw)
        if heading is None or not 0 <= heading < 360:
            raise ValueError(f"stadium orientation venue {venue} heading is invalid")
        output[venue] = heading
    return output


STADIUM_ORIENTATIONS = load_stadium_orientations()


def relative_wind_direction(
    stadium_number: Any,
    wind_direction_number: Any,
    wind_speed: Any,
    *,
    orientations: Mapping[int, float | None] = STADIUM_ORIENTATIONS,
) -> str | None:
    """Classify a meteorological wind source relative to boat travel.

    BOATRACE's 16-point code is interpreted as the compass bearing the wind
    blows *from* (1=north, clockwise in 22.5-degree steps).  The venue master
    stores the bearing boats travel from turn 1 toward turn 2.  Therefore a
    signed shortest angle ``theta = wind_from - travel_heading`` of +/-45
    degrees is a headwind; absolute theta from 135 through 180 degrees is a
    tailwind.  Remaining positive/clockwise angles are right crosswinds and
    negative/counter-clockwise angles are left crosswinds.  Both 45 and 135
    degree boundaries belong to the head/tail bands respectively.
    """

    try:
        venue = int(stadium_number)
        raw_direction = int(wind_direction_number)
    except (TypeError, ValueError):
        return None
    speed = _finite_float(wind_speed)
    if speed == 0 or raw_direction == 17:
        return "無風"
    heading = orientations.get(venue)
    if heading is None or speed is None or not 1 <= raw_direction <= 16:
        return None
    wind_from = (raw_direction - 1) * 22.5
    theta = (wind_from - heading + 180.0) % 360.0 - 180.0
    absolute = abs(theta)
    if absolute <= 45.0:
        return "向かい風"
    if absolute >= 135.0:
        return "追い風"
    return "横風(右)" if theta > 0 else "横風(左)"


def _day_indexes(source: Any, races: list[dict[str, Any]]) -> dict[str, str | None]:
    keys = {(int(r["stadium_number"]), str(r.get("race_title") or "")) for r in races}
    output: dict[str, str | None] = {}
    for stadium, title in keys:
        if not title:
            continue
        dates = [
            _iso(row["race_date"])
            for row in _rows(
                source,
                """SELECT DISTINCT race_date FROM races
                    WHERE stadium_number=? AND race_title=? ORDER BY race_date""",
                (stadium, title),
            )
        ]
        if not dates:
            continue
        # The same event title can recur in later years.  Split title-matched
        # dates into meetings when more than one empty calendar day separates
        # them; a single weather-cancellation day can still remain one meeting.
        meetings: list[list[str]] = []
        for value in dates:
            if not meetings or (
                date.fromisoformat(value) - date.fromisoformat(meetings[-1][-1])
            ).days > 2:
                meetings.append([value])
            else:
                meetings[-1].append(value)
        label_by_date: dict[str, str] = {}
        for meeting in meetings:
            for index, value in enumerate(meeting):
                label_by_date[value] = (
                    "初日"
                    if index == 0
                    else "最終日"
                    if index == len(meeting) - 1
                    else "中日"
                )
        for race in races:
            if int(race["stadium_number"]) == stadium and str(race.get("race_title") or "") == title:
                output[str(race["race_id"])] = label_by_date.get(_iso(race["race_date"]))
    return output


def _class_mix(entries: dict[int, dict[str, Any]]) -> str | None:
    if set(entries) != set(BOATS) or any(row.get("class_number") is None for row in entries.values()):
        return None
    a1 = [boat for boat, row in entries.items() if int(row["class_number"]) == 1]
    if len(a1) == 1:
        return "1号艇A1" if a1[0] == 1 else "A1単騎"
    if len(a1) > 1:
        return "1号艇A1" if 1 in a1 else "A1複数_1号艇非A1"
    return "A1なし"


def _female_present(entries: dict[int, dict[str, Any]]) -> int | None:
    if set(entries) != set(BOATS):
        return None
    genders = [row.get("gender") for row in entries.values()]
    if any(value not in (1, 2) for value in genders):
        return None
    return int(2 in genders)


def _normalize_combination(value: Any, legs: int) -> str | None:
    """Return the canonical hyphenated boat combination for a payout row."""

    if value in (None, "不成立"):
        return None
    normalized = str(value).strip()
    for separator in ("－", "−", "ー", "―", "‐", "ｰ", " "):
        normalized = normalized.replace(separator, "-")
    parts = [part.strip() for part in normalized.split("-") if part.strip()]
    if len(parts) != legs:
        return None
    try:
        boats = [int(part) for part in parts]
    except ValueError:
        return None
    if any(boat not in BOATS for boat in boats) or len(set(boats)) != len(boats):
        return None
    return "-".join(str(boat) for boat in boats)


def _winning_combinations(
    results: list[dict[str, Any]], legs: int
) -> tuple[list[str] | None, str | None]:
    """Derive every official winning ticket implied by finishing positions.

    Tied boats may appear in either order within the tied rank.  Competition
    ranking must remain contiguous (for example 1,1,3 or 1,2,2,4); conflicting
    versions for one boat or a rank gap make the order ambiguous.
    """

    positions_by_boat: dict[int, set[int]] = defaultdict(set)
    for item in results:
        try:
            boat = int(item.get("boat_number"))
            position = int(item.get("finishing_position"))
        except (TypeError, ValueError):
            continue
        if boat in BOATS and position >= 1:
            positions_by_boat[boat].add(position)
    if any(len(positions) > 1 for positions in positions_by_boat.values()):
        return None, "conflicting result versions"

    groups: dict[int, list[int]] = defaultdict(list)
    for boat, positions in positions_by_boat.items():
        if positions:
            groups[next(iter(positions))].append(boat)
    ordered_groups: list[list[int]] = []
    prior_boats = 0
    for position in sorted(groups):
        if position != prior_boats + 1:
            return None, "ambiguous finishing-position gap"
        group = sorted(groups[position])
        ordered_groups.append(group)
        prior_boats += len(group)
        if prior_boats >= legs:
            break
    if prior_boats < legs:
        return None, "insufficient finishing positions"

    tickets = {
        "-".join(str(boat) for boat in order[:legs])
        for order in (
            tuple(boat for group_order in group_orders for boat in group_order)
            for group_orders in product(
                *(tuple(permutations(group)) for group in ordered_groups)
            )
        )
    }
    return sorted(tickets), None


def _winning_payouts(
    results: list[dict[str, Any]],
    payouts: list[dict[str, Any]],
    kind: str,
    legs: int,
) -> tuple[list[str] | None, dict[str, int] | None, str | None]:
    winners, error = _winning_combinations(results, legs)
    if winners is None:
        return None, None, error
    matching: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in payouts:
        if str(item.get("bet_type")) != kind:
            continue
        combination = _normalize_combination(item.get("combination"), legs)
        if combination in winners:
            matching[combination].append(item)
    amounts: dict[str, int] = {}
    for winner in winners:
        rows = matching[winner]
        if len(rows) != 1:
            return None, None, f"expected one payout for {winner}, found {len(rows)}"
        try:
            amounts[winner] = int(rows[0]["payout"])
        except (KeyError, TypeError, ValueError):
            return None, None, f"invalid payout for {winner}"
    return winners, amounts, None


def _build_row(
    race: dict[str, Any],
    entries: list[dict[str, Any]],
    previews: list[dict[str, Any]],
    tide: dict[str, Any] | None,
    payouts: list[dict[str, Any]],
    results: list[dict[str, Any]],
    histories: dict[int, RacerHistory],
    period_accidents: Mapping[str, Any],
    restored_accidents: Mapping[int, RestoredAccidentHistory],
    restored_start_timings: Mapping[int, RestoredStartTimingHistory],
    t5_favorite_odds: Mapping[str, float],
    day_index: str | None,
    built_at: str,
    warning_messages: list[str] | None = None,
) -> dict[str, Any]:
    race_date = _iso(race["race_date"])
    asof_date = (date.fromisoformat(race_date) - timedelta(days=1)).isoformat()
    entry_by_boat = {int(item["boat_number"]): item for item in entries}
    preview_by_boat = {int(item["boat_number"]): item for item in previews}
    first_deadline = race.get("first_race_closed_at")
    preview_header = preview_by_boat.get(1) or next(iter(preview_by_boat.values()), {})
    times = {
        boat: _finite_float(preview_by_boat.get(boat, {}).get("exhibition_time"))
        for boat in BOATS
    }
    metrics = exhibition_metrics(times)
    row: dict[str, Any] = {
        "race_id": str(race["race_id"]),
        "race_date": race_date,
        "asof_date": asof_date,
        "built_at": built_at,
        "schema_version": SCHEMA_VERSION,
        "jcd": race.get("stadium_number"),
        "race_no": race.get("race_number"),
        "grade": race.get("race_grade_number"),
        "day_index": day_index,
        "daypart": classify_daypart(first_deadline),
        "female_present": _female_present(entry_by_boat),
        "class_mix": _class_mix(entry_by_boat),
        "tide_phase": normalize_tide_phase(tide),
        "weather": weather_label(preview_header.get("weather_number")),
        "wind_dir": relative_wind_direction(
            race.get("stadium_number"),
            preview_header.get("wind_direction_number"),
            preview_header.get("wind_speed"),
        ),
        "wind_dir_raw": preview_header.get("wind_direction_number"),
        "wind_speed": _finite_float(preview_header.get("wind_speed")),
        "t5_odds_favorite": t5_favorite_odds.get(str(race["race_id"])),
    }
    for boat in BOATS:
        entry = entry_by_boat.get(boat, {})
        preview = preview_by_boat.get(boat, {})
        rates = _history_rates(histories, entry.get("racer_number"), asof_date)
        accident_rate, accident_points, accident_source = _period_accident_values(
            period_accidents, race_date, entry.get("racer_number")
        )
        restored_rate, restored_count, restored_starts = (
            _restored_period_accident_values(
                restored_accidents, race_date, entry.get("racer_number")
            )
        )
        restored_avg_st, restored_avg_st_n = _restored_average_start_timing_values(
            restored_start_timings, race_date, entry.get("racer_number")
        )
        row.update(
            {
                f"b{boat}_racer_id": entry.get("racer_number"),
                f"b{boat}_class": CLASS_LABELS.get(entry.get("class_number")),
                f"b{boat}_age": _age_on_date(entry.get("birth_date"), race_date),
                f"b{boat}_avg_st": restored_avg_st,
                f"b{boat}_avg_st_n": restored_avg_st_n,
                f"b{boat}_avg_st_official": _finite_float(
                    entry.get("avg_start_timing")
                ),
                f"b{boat}_national_rate": _finite_float(entry.get("national_top_1_percent")),
                f"b{boat}_local_rate": _finite_float(entry.get("local_top_1_percent")),
                f"b{boat}_national_rate2": _finite_float(entry.get("national_top_2_percent")),
                f"b{boat}_local_rate2": _finite_float(entry.get("local_top_2_percent")),
                f"b{boat}_motor_rate2": _finite_float(entry.get("assigned_motor_top_2_percent")),
                f"b{boat}_ex_time": times[boat],
                f"b{boat}_ex_rank": metrics[boat][0],
                f"b{boat}_ex_dev": metrics[boat][1],
                # F is stored as a negative number by the source parser; L or
                # an unparseable exhibition start remains NULL.
                f"b{boat}_ex_st": _finite_float(preview.get("start_timing_exhibition")),
                **{
                    f"b{boat}_kimarite_rate_{key}": rates[key]
                    for key in KIMARITE_KEYS
                },
                f"b{boat}_accident_rate": accident_rate,
                f"b{boat}_accident_rate_365d": rates["accident"],
                f"b{boat}_accident_points": accident_points,
                f"b{boat}_accident_source": accident_source,
                f"b{boat}_accident_rate_period": restored_rate,
                f"b{boat}_accident_count_period": restored_count,
                f"b{boat}_starts_period": restored_starts,
            }
        )
    messages = warning_messages if warning_messages is not None else []
    has_results = any(item.get("finishing_position") is not None for item in results)
    for kind, legs in (("sanrentan", 3), ("nirentan", 2), ("tansho", 1)):
        winners: list[str] | None = None
        amounts: dict[str, int] | None = None
        error: str | None = None
        if has_results:
            source_kind = {"sanrentan": "trifecta", "nirentan": "exacta", "tansho": "win"}[kind]
            winners, amounts, error = _winning_payouts(results, payouts, source_kind, legs)
        if error is not None:
            messages.append(f"{kind}: {error}")
        representative = winners[0] if winners and amounts else None
        row[f"result_{kind}"] = (
            int(representative) if kind == "tansho" and representative is not None else representative
        )
        row[f"payout_{kind}"] = amounts[representative] if representative and amounts else None
        row[f"result_{kind}_json"] = (
            json.dumps(winners, separators=(",", ":")) if winners and amounts else None
        )
        row[f"payout_{kind}_json"] = (
            json.dumps(amounts, separators=(",", ":"), sort_keys=True)
            if winners and amounts
            else None
        )
    return row


def build_features(
    source: Any,
    output_path: str | Path,
    date_from: str,
    date_to: str,
    *,
    rebuild: bool = False,
    built_at: str | None = None,
    progress_stream: Any = sys.stdout,
) -> dict[str, int]:
    """Build a date-ascending append-only snapshot batch.

    Each racer aggregate uses the inclusive 365-day window
    ``[asof_date - 364 days, asof_date]``.  Results on the target race date are
    never eligible for those aggregates.
    """

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if start > end:
        raise ValueError("date_from must not be after date_to")
    races = _rows(
        source,
        """SELECT r.*,
                  (SELECT MIN(r1.race_closed_at) FROM races r1
                    WHERE r1.race_date=r.race_date
                      AND r1.stadium_number=r.stadium_number) AS first_race_closed_at
             FROM races r
            WHERE r.race_date BETWEEN ? AND ?
            ORDER BY r.race_date, r.stadium_number, r.race_number""",
        (date_from, date_to),
    )
    output = open_output(output_path)
    if rebuild:
        output.execute(
            "DELETE FROM asof_race_features WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        )
        output.commit()
    existing = {
        row[0]
        for row in output.execute(
            "SELECT race_id FROM asof_race_features WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        )
    }
    pending = [race for race in races if str(race["race_id"]) not in existing]
    if not pending:
        output.close()
        return {"selected": len(races), "inserted": 0, "skipped_existing": len(races), "warnings": 0}

    race_ids = [str(race["race_id"]) for race in pending]
    entry_rows = _rows_for_ids(
        source,
        """SELECT e.*, rc.gender, rc.birth_date FROM race_entries e
          LEFT JOIN racers rc ON rc.racer_number=e.racer_number
               WHERE e.race_id IN ({placeholders})
            ORDER BY e.race_id,e.boat_number""",
        race_ids,
    )
    preview_rows = _rows_for_ids(
        source,
        "SELECT * FROM race_previews WHERE race_id IN ({placeholders}) ORDER BY race_id,boat_number",
        race_ids,
    )
    tide_rows = _rows_for_ids(
        source,
        "SELECT race_id,tide_phase,is_high_tide_zone,is_low_tide_zone FROM race_tides WHERE race_id IN ({placeholders})",
        race_ids,
    )
    payout_rows = _rows_for_ids(
        source,
        "SELECT race_id,bet_type,combination,payout FROM race_payouts WHERE race_id IN ({placeholders})",
        race_ids,
    )
    result_rows = _rows_for_ids(
        source,
        "SELECT race_id,boat_number,finishing_position FROM race_results "
        "WHERE race_id IN ({placeholders}) ORDER BY race_id,boat_number",
        race_ids,
    )
    by_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_previews: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_payouts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in entry_rows:
        by_entries[str(item["race_id"])].append(item)
    for item in preview_rows:
        by_previews[str(item["race_id"])].append(item)
    for item in payout_rows:
        by_payouts[str(item["race_id"])].append(item)
    for item in result_rows:
        by_results[str(item["race_id"])].append(item)
    by_tide = {str(item["race_id"]): item for item in tide_rows}
    racer_ids = {
        int(item["racer_number"])
        for item in entry_rows
        if item.get("racer_number") is not None
    }
    earliest_asof = start - timedelta(days=1)
    history_start = earliest_asof - timedelta(days=364)
    histories = _load_histories(
        source,
        history_start.isoformat(),
        (end - timedelta(days=1)).isoformat(),
        racer_ids,
    )
    period_accidents = _load_period_accidents(source, pending, racer_ids)
    restored_accidents = load_restored_histories(
        output,
        min(_accident_period_start_for_date(_iso(race["race_date"])) for race in pending),
        max(_iso(race["race_date"]) for race in pending),
        racer_ids,
    )
    restored_start_timings = load_start_timing_histories(
        output,
        (start - timedelta(days=180)).isoformat(),
        max(_iso(race["race_date"]) for race in pending),
        racer_ids,
    )
    t5_favorite_odds = _load_t5_favorite_odds(source, race_ids)
    day_indexes = _day_indexes(source, pending)
    timestamp = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    names = [name for name, _ in ALL_COLUMNS]
    sql = (
        f"INSERT INTO asof_race_features ({','.join(names)}) "
        f"VALUES ({','.join('?' for _ in names)})"
    )
    inserted = 0
    warnings = 0
    for race in pending:
        race_id = str(race["race_id"])
        try:
            race_warnings: list[str] = []
            row = _build_row(
                race,
                by_entries[race_id],
                by_previews[race_id],
                by_tide.get(race_id),
                by_payouts[race_id],
                by_results[race_id],
                histories,
                period_accidents,
                restored_accidents,
                restored_start_timings,
                t5_favorite_odds,
                day_indexes.get(race_id),
                timestamp,
                race_warnings,
            )
            output.execute(sql, [row.get(name) for name in names])
            inserted += 1
            for message in race_warnings:
                warnings += 1
                print(f"warning: {race_id}: {message}", file=progress_stream, flush=True)
            if inserted % 1000 == 0:
                print(f"processed {inserted:,} races", file=progress_stream, flush=True)
                output.commit()
        except Exception as exc:  # one bad race must not abort the batch
            warnings += 1
            print(f"warning: skipped {race_id}: {exc}", file=progress_stream, flush=True)
    output.commit()
    output.close()
    return {
        "selected": len(races),
        "inserted": inserted,
        "skipped_existing": len(races) - len(pending),
        "warnings": warnings,
    }


def _equal_rate(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-10)


def verify_features(
    source: Any,
    output_path: str | Path,
    sample: int = 20,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Verify chronology and sampled future-data exclusion invariance."""

    output = sqlite3.connect(output_path)
    output.row_factory = sqlite3.Row
    where: list[str] = []
    params: list[Any] = []
    if date_from is not None:
        where.append("race_date>=?")
        params.append(date_from)
    if date_to is not None:
        where.append("race_date<=?")
        params.append(date_to)
    clause = " WHERE " + " AND ".join(where) if where else ""
    chronology_errors = output.execute(
        "SELECT COUNT(*) FROM asof_race_features WHERE asof_date>=race_date"
    ).fetchone()[0]
    candidates = output.execute(
        "SELECT * FROM asof_race_features" + clause + " ORDER BY race_id", params
    ).fetchall()
    if sample < 0:
        raise ValueError("sample must be non-negative")
    chosen = random.Random(20260815).sample(candidates, min(sample, len(candidates)))
    racer_ids = {
        int(row[f"b{boat}_racer_id"])
        for row in chosen
        for boat in BOATS
        if row[f"b{boat}_racer_id"] is not None
    }
    histories: dict[int, RacerHistory] = {}
    if chosen:
        earliest = min(date.fromisoformat(row["asof_date"]) for row in chosen)
        latest = max(date.fromisoformat(row["asof_date"]) for row in chosen)
        histories = _load_histories(
            source,
            (earliest - timedelta(days=364)).isoformat(),
            latest.isoformat(),
            racer_ids,
        )
    chosen_races = [dict(row) for row in chosen]
    period_accidents = _load_period_accidents(source, chosen_races, racer_ids)
    restored_accidents: dict[int, RestoredAccidentHistory] = {}
    restored_start_timings: dict[int, RestoredStartTimingHistory] = {}
    if chosen_races:
        restored_accidents = load_restored_histories(
            output,
            min(
                _accident_period_start_for_date(str(race["race_date"]))
                for race in chosen_races
            ),
            max(str(race["race_date"]) for race in chosen_races),
            racer_ids,
        )
        restored_start_timings = load_start_timing_histories(
            output,
            (
                min(date.fromisoformat(str(race["race_date"])) for race in chosen_races)
                - timedelta(days=180)
            ).isoformat(),
            max(str(race["race_date"]) for race in chosen_races),
            racer_ids,
        )
    mismatches: list[str] = []
    for row in chosen:
        for boat in BOATS:
            rates = _history_rates(histories, row[f"b{boat}_racer_id"], row["asof_date"])
            for key in KIMARITE_KEYS:
                column = f"b{boat}_kimarite_rate_{key}"
                if not _equal_rate(row[column], rates[key]):
                    mismatches.append(f"{row['race_id']}:{column}")
            column = f"b{boat}_accident_rate_365d"
            if column in row.keys() and not _equal_rate(row[column], rates["accident"]):
                mismatches.append(f"{row['race_id']}:{column}")
            if int(row["schema_version"]) >= 5:
                rate, points, source_kind = _period_accident_values(
                    period_accidents,
                    str(row["race_date"]),
                    row[f"b{boat}_racer_id"],
                )
                for period_column, expected in (
                    (f"b{boat}_accident_rate", rate),
                    (f"b{boat}_accident_points", points),
                    (f"b{boat}_accident_source", source_kind),
                ):
                    actual = row[period_column]
                    equal = (
                        actual == expected
                        if isinstance(expected, str)
                        else _equal_rate(actual, expected)
                    )
                    if not equal:
                        mismatches.append(f"{row['race_id']}:{period_column}")
            if int(row["schema_version"]) >= 6:
                rate, count, starts = _restored_period_accident_values(
                    restored_accidents,
                    str(row["race_date"]),
                    row[f"b{boat}_racer_id"],
                )
                for period_column, expected in (
                    (f"b{boat}_accident_rate_period", rate),
                    (f"b{boat}_accident_count_period", count),
                    (f"b{boat}_starts_period", starts),
                ):
                    actual = row[period_column]
                    equal = (
                        actual == expected
                        if isinstance(expected, int)
                        else _equal_rate(actual, expected)
                    )
                    if not equal:
                        mismatches.append(f"{row['race_id']}:{period_column}")
            if int(row["schema_version"]) >= 7:
                average, count = _restored_average_start_timing_values(
                    restored_start_timings,
                    str(row["race_date"]),
                    row[f"b{boat}_racer_id"],
                )
                if not _equal_rate(row[f"b{boat}_avg_st"], average):
                    mismatches.append(f"{row['race_id']}:b{boat}_avg_st")
                if row[f"b{boat}_avg_st_n"] != count:
                    mismatches.append(f"{row['race_id']}:b{boat}_avg_st_n")
    output.close()
    return {
        "ok": chronology_errors == 0 and not mismatches,
        "rows": len(candidates),
        "sampled": len(chosen),
        "chronology_errors": chronology_errors,
        "mismatches": mismatches,
    }


def coverage_rows(output_path: str | Path) -> list[dict[str, Any]]:
    """Return non-NULL count, earliest populated race date, and fill percent."""

    conn = sqlite3.connect(output_path)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(asof_race_features)")]
    total = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
    result: list[dict[str, Any]] = []
    for column in columns:
        quoted = '"' + column.replace('"', '""') + '"'
        populated, oldest = conn.execute(
            f"SELECT COUNT({quoted}), MIN(CASE WHEN {quoted} IS NOT NULL THEN race_date END) "
            "FROM asof_race_features"
        ).fetchone()
        result.append(
            {
                "column": column,
                "populated": populated,
                "total": total,
                "oldest_date": oldest,
                "coverage_pct": (populated * 100.0 / total) if total else 0.0,
            }
        )
    conn.close()
    return result
