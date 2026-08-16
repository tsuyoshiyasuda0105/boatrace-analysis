"""Restore racer accident history from official fixed-width K result files.

Responsibility classification follows the repository's existing
``scripts/rebuild_racer_accident_stats.py`` rules: F and L/L0/L1 are counted,
K1/S1/S2 are racer-responsible, and K0/S0 are explicitly non-responsible.
Any other non-numeric result code is preserved with ``is_accident=0`` so an
unknown code can never be silently mixed into the numerator.

The established :func:`src.parsers.official_k._parse_result_row` remains the
single full-row parser.  Its current regular expression rejects F rows whose
ST field is written as ``F0.02`` and does not accept L0/L1 ranks, so those
tokens are normalized before one call.  A narrow fixed-width prefix fallback
recovers only rank/boat/racer for genuinely field-less rows and reports every
full-row-parser miss; it never guesses course or ST.

No accident points are assigned.  The only repository point table located is
the explicitly reconstructed rule set applying from 2025-05-01, which is not
evidence that those values can be applied retrospectively to the ten-year
archive.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Iterator, Sequence

from src.parsers.official_k import (
    _extract_race_no,
    _extract_stadium,
    _parse_result_row,
    _stadium_name_to_number,
    _to_half,
)


RESPONSIBLE_CODES = frozenset({"F", "L", "L0", "L1", "K1", "S1", "S2"})
NON_RESPONSIBLE_CODES = frozenset({"K0", "S0"})
RESULT_PREFIX_RE = re.compile(r"^  (.{2})\s+([1-6])\s+(\d{4})\s+")
EARLY_LATE_ST_RE = re.compile(r"(?<=\s)([FL])(\d+\.\d{2})(?=\s|$)")
STADIUM_CONTROL_RE = re.compile(r"^(\d{2})KBGN\s*$")
K_FILE_RE = re.compile(r"^[Kk](\d{6})$")
DEFAULT_7ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
SQLITE_CHUNK_SIZE = 900


@dataclass(frozen=True)
class AccidentEvent:
    race_id: str
    race_date: str
    racer_number: int
    boat_number: int
    code: str
    is_accident: int


@dataclass(frozen=True)
class RacerStart:
    race_id: str
    race_date: str
    racer_number: int


@dataclass(frozen=True)
class StartTimingEvent:
    """One measured start from an official K result row.

    The sign convention is fixed here and in tests: an ordinary ``0.14`` is
    positive, while ``F0.02`` (0.02 seconds early) is stored as ``-0.02`` with
    ``is_flying=1``.  Late starts and missing ``.`` values remain NULL.
    """

    race_id: str
    race_date: str
    racer_number: int
    boat_number: int
    course_number: int | None
    start_timing: float | None
    is_flying: int
    is_late: int


@dataclass
class ParseDiagnostics:
    race_count: int = 0
    candidate_rows: int = 0
    primary_parser_rows: int = 0
    fallback_rows: int = 0
    skipped_rows: int = 0
    incomplete_races: int = 0
    unknown_codes: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedResultFile:
    starts: tuple[RacerStart, ...]
    events: tuple[AccidentEvent, ...]
    start_timings: tuple[StartTimingEvent, ...]
    diagnostics: ParseDiagnostics


@dataclass
class RestoreSummary:
    files_selected: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    rows_seen: int = 0
    fallback_rows: int = 0
    rows_skipped: int = 0
    incomplete_races: int = 0
    starts_found: int = 0
    starts_inserted: int = 0
    events_found: int = 0
    events_inserted: int = 0
    responsible_events: int = 0
    code_counts: Counter[str] = field(default_factory=Counter)
    unknown_code_counts: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


@dataclass
class StartTimingRestoreSummary:
    files_selected: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    rows_seen: int = 0
    fallback_rows: int = 0
    rows_skipped: int = 0
    incomplete_races: int = 0
    events_found: int = 0
    events_inserted: int = 0
    normal_valid: int = 0
    flying: int = 0
    late: int = 0
    missing: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RestoredAccidentHistory:
    """Chronological starts/events for one racer, queried with an exclusive end."""

    start_dates: tuple[str, ...]
    accident_dates: tuple[str, ...]

    def period_values(
        self, period_start: str, race_date: str
    ) -> tuple[float | None, int, int]:
        """Return rate, accident count and starts in ``[start, race_date)``."""

        starts = bisect_left(self.start_dates, race_date) - bisect_left(
            self.start_dates, period_start
        )
        accidents = bisect_left(self.accident_dates, race_date) - bisect_left(
            self.accident_dates, period_start
        )
        rate = accidents * 100.0 / starts if starts else None
        return rate, accidents, starts


@dataclass(frozen=True)
class RestoredStartTimingHistory:
    """Chronological valid non-F/non-L starts with prefix sums."""

    dates: tuple[str, ...]
    cumulative: tuple[float, ...]

    @classmethod
    def from_rows(
        cls, rows: Iterable[tuple[str, float]]
    ) -> "RestoredStartTimingHistory":
        dates: list[str] = []
        cumulative = [0.0]
        for event_date, timing in rows:
            dates.append(event_date)
            cumulative.append(cumulative[-1] + timing)
        return cls(tuple(dates), tuple(cumulative))

    def average(self, period_start: str, race_date: str) -> tuple[float | None, int]:
        """Return the ordinary-ST mean in ``[period_start, race_date)``."""

        left = bisect_left(self.dates, period_start)
        right = bisect_left(self.dates, race_date)
        count = right - left
        if count == 0:
            return None, 0
        return (self.cumulative[right] - self.cumulative[left]) / count, count


def classify_accident_code(code: str) -> int:
    """Return 1 only for repository-supported racer-responsible codes."""

    normalized = str(code or "").strip().upper()
    return int(normalized in RESPONSIBLE_CODES)


def _date_from_stem(stem: str) -> date | None:
    match = K_FILE_RE.fullmatch(stem)
    if not match:
        return None
    raw = match.group(1)
    try:
        return date(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
    except ValueError:
        return None


def _parse_shared_result_row(
    line: str, prefix: re.Match[str]
) -> tuple[str, dict[str, Any] | None]:
    """Parse a result row once through the established official-K parser.

    The parser's historical regex does not accept the archive spellings
    ``F0.02`` or rank ``L0``/``L1``.  Those tokens are normalized without
    interpreting field positions, then the same ``_parse_result_row`` path is
    called exactly once.  A genuinely unparseable cancellation row falls back
    only to the already matched rank/boat/racer prefix and therefore has NULL
    course/ST rather than a guessed value.
    """

    code = prefix.group(1).strip().upper()
    normalized = line
    if code in {"L0", "L1"}:
        normalized = normalized[:2] + "L " + normalized[4:]

    def replace_early_late(match: re.Match[str]) -> str:
        marker, value = match.groups()
        return f"-{value}" if marker == "F" else "."

    normalized = EARLY_LATE_ST_RE.sub(replace_early_late, normalized)
    return code, _parse_result_row(normalized)


def parse_official_result_text(text: str, race_date: date) -> ParsedResultFile:
    """Parse starts, accidents and measured ST through one shared row path."""

    diagnostics = ParseDiagnostics()
    stadium_map = _stadium_name_to_number()
    uses_control_markers = any(
        STADIUM_CONTROL_RE.fullmatch(line.strip()) for line in text.splitlines()
    )
    current_stadium: int | None = None
    current_race_id: str | None = None
    starts: list[RacerStart] = []
    events: list[AccidentEvent] = []
    start_timings: list[StartTimingEvent] = []
    starts_per_race: Counter[str] = Counter()
    seen_starts: set[tuple[str, int]] = set()

    for line_number, line in enumerate(text.splitlines(), 1):
        control = STADIUM_CONTROL_RE.fullmatch(line.strip())
        if control is not None:
            current_stadium = int(control.group(1))
            continue
        if not uses_control_markers:
            stadium = _extract_stadium(line, stadium_map)
            if stadium is not None:
                current_stadium = stadium
                continue
        race_number = _extract_race_no(line)
        if (
            race_number is not None
            and current_stadium is not None
            and "H" in _to_half(line)
        ):
            current_race_id = (
                f"{race_date.strftime('%Y%m%d')}-{current_stadium:02d}-{race_number:02d}"
            )
            continue

        match = RESULT_PREFIX_RE.match(line)
        if match is None:
            continue
        diagnostics.candidate_rows += 1
        if current_race_id is None:
            diagnostics.skipped_rows += 1
            diagnostics.warnings.append(
                f"line {line_number}: result row has no race context"
            )
            continue
        code, result = _parse_shared_result_row(line, match)
        boat_number = int(result["boat_number"]) if result is not None else int(match.group(2))
        racer_number = int(result["racer_number"]) if result is not None else int(match.group(3))
        key = (current_race_id, racer_number)
        if key in seen_starts:
            diagnostics.skipped_rows += 1
            diagnostics.warnings.append(
                f"line {line_number}: duplicate racer {racer_number} in {current_race_id}"
            )
            continue
        seen_starts.add(key)
        starts_per_race[current_race_id] += 1
        starts.append(RacerStart(current_race_id, race_date.isoformat(), racer_number))
        if result is None:
            diagnostics.fallback_rows += 1
        else:
            diagnostics.primary_parser_rows += 1

        is_flying = int(code == "F")
        is_late = int(code in {"L", "L0", "L1"})
        timing = result.get("start_timing") if result is not None else None
        if is_late:
            timing = None
        elif is_flying and timing is not None:
            timing = -abs(float(timing))
        elif timing is not None:
            timing = float(timing)
        start_timings.append(
            StartTimingEvent(
                current_race_id,
                race_date.isoformat(),
                racer_number,
                boat_number,
                int(result["course_number"]) if result is not None else None,
                timing,
                is_flying,
                is_late,
            )
        )

        if code.isdigit() and 1 <= int(code) <= 6:
            continue
        is_accident = classify_accident_code(code)
        if code not in RESPONSIBLE_CODES and code not in NON_RESPONSIBLE_CODES:
            diagnostics.unknown_codes[code] += 1
            diagnostics.warnings.append(
                f"line {line_number}: unknown result code {code!r} kept as non-accident"
            )
        events.append(
            AccidentEvent(
                current_race_id,
                race_date.isoformat(),
                racer_number,
                boat_number,
                code,
                is_accident,
            )
        )

    # Only result-bearing races have starts.  The legacy parser can emit
    # duplicate/misattributed IDs around an all-races-cancelled venue block,
    # so it is intentionally not authoritative for denominator race IDs.
    all_race_ids = set(starts_per_race)
    diagnostics.race_count = len(all_race_ids)
    for race_id in sorted(all_race_ids):
        count = starts_per_race[race_id]
        if count != 6:
            diagnostics.incomplete_races += 1
            diagnostics.warnings.append(
                f"{race_id}: expected 6 start rows, found {count}"
            )
    return ParsedResultFile(tuple(starts), tuple(events), tuple(start_timings), diagnostics)


def parse_official_result_file(path: str | Path) -> ParsedResultFile:
    source = Path(path)
    race_date = _date_from_stem(source.stem)
    if race_date is None:
        raise ValueError(f"invalid K result filename: {source.name}")
    return parse_official_result_text(source.read_text(encoding="cp932"), race_date)


def ensure_accident_history_schema(conn: sqlite3.Connection) -> None:
    """Create only the two Step 13 tables and their required indexes."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accident_events (
          race_id TEXT NOT NULL,
          race_date TEXT NOT NULL,
          racer_number INTEGER NOT NULL,
          boat_number INTEGER,
          code TEXT NOT NULL,
          is_accident INTEGER NOT NULL,
          PRIMARY KEY (race_id, racer_number)
        );
        CREATE INDEX IF NOT EXISTS idx_accident_racer_date
          ON accident_events(racer_number, race_date);
        CREATE TABLE IF NOT EXISTS racer_starts (
          race_id TEXT NOT NULL,
          race_date TEXT NOT NULL,
          racer_number INTEGER NOT NULL,
          PRIMARY KEY (race_id, racer_number)
        );
        CREATE INDEX IF NOT EXISTS idx_starts_racer_date
          ON racer_starts(racer_number, race_date);
        """
    )


def ensure_start_timing_schema(conn: sqlite3.Connection) -> None:
    """Create only the Step 15 measured-start table and index."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS start_timing_events (
          race_id TEXT NOT NULL,
          race_date TEXT NOT NULL,
          racer_number INTEGER NOT NULL,
          boat_number INTEGER,
          course_number INTEGER,
          start_timing REAL,
          is_flying INTEGER NOT NULL,
          is_late INTEGER NOT NULL,
          PRIMARY KEY (race_id, racer_number)
        );
        CREATE INDEX IF NOT EXISTS idx_st_racer_date
          ON start_timing_events(racer_number, race_date);
        """
    )


def _selected_sources(
    raw_dir: Path, date_from: date, date_to: date
) -> list[tuple[date, Path]]:
    selected: dict[date, Path] = {}
    priority: dict[date, int] = {}
    for path in raw_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".txt", ".lzh"}:
            continue
        source_date = _date_from_stem(path.stem)
        if source_date is None or not date_from <= source_date <= date_to:
            continue
        item_priority = 0 if path.suffix.lower() == ".txt" else 1
        if source_date not in selected or item_priority < priority[source_date]:
            selected[source_date] = path
            priority[source_date] = item_priority
    return sorted(selected.items())


def _parse_lzh(path: Path, seven_zip: Path) -> ParsedResultFile:
    if not seven_zip.is_file():
        raise FileNotFoundError(f"7-Zip not found: {seven_zip}")
    with tempfile.TemporaryDirectory(prefix="kachisuji-accident-") as temporary:
        destination = Path(temporary)
        completed = subprocess.run(
            [str(seven_zip), "x", "-y", f"-o{destination}", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"7-Zip failed ({completed.returncode}): {detail}")
        extracted = sorted(destination.rglob("K*.TXT")) + sorted(
            destination.rglob("k*.txt")
        )
        if len(extracted) != 1:
            raise RuntimeError(f"expected one K TXT in {path.name}, found {len(extracted)}")
        return parse_official_result_file(extracted[0])


def restore_accident_history(
    db_path: str | Path,
    raw_dir: str | Path,
    date_from: str,
    date_to: str,
    *,
    rebuild: bool = False,
    seven_zip: str | Path = DEFAULT_7ZIP,
    progress: Callable[[str], None] | None = print,
) -> RestoreSummary:
    """Restore a bounded date range append-only, or replace it with rebuild."""

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if start > end:
        raise ValueError("date_from must not be after date_to")
    sources = _selected_sources(Path(raw_dir), start, end)
    summary = RestoreSummary(files_selected=len(sources))
    resolved_db = Path(db_path).resolve()
    if resolved_db.name.lower() == "boatrace.db":
        raise ValueError("refusing to write accident history to data/boatrace.db")
    connection = sqlite3.connect(resolved_db)
    try:
        with connection:
            ensure_accident_history_schema(connection)
            for index, (source_date, path) in enumerate(sources, 1):
                try:
                    parsed = (
                        parse_official_result_file(path)
                        if path.suffix.lower() == ".txt"
                        else _parse_lzh(path, Path(seven_zip))
                    )
                except Exception as exc:
                    summary.files_skipped += 1
                    message = f"warning: skipped file {path.name}: {exc}"
                    summary.warnings.append(message)
                    if progress is not None:
                        progress(message)
                    continue
                summary.files_parsed += 1
                diag = parsed.diagnostics
                summary.rows_seen += diag.candidate_rows
                summary.fallback_rows += diag.fallback_rows
                summary.rows_skipped += diag.skipped_rows
                summary.incomplete_races += diag.incomplete_races
                summary.starts_found += len(parsed.starts)
                summary.events_found += len(parsed.events)
                summary.responsible_events += sum(e.is_accident for e in parsed.events)
                summary.code_counts.update(e.code for e in parsed.events)
                summary.unknown_code_counts.update(diag.unknown_codes)
                summary.warnings.extend(f"{path.name}: {item}" for item in diag.warnings)
                if progress is not None:
                    for item in diag.warnings:
                        progress(f"warning: {path.name}: {item}")

                if rebuild:
                    day = source_date.isoformat()
                    connection.execute(
                        "DELETE FROM accident_events WHERE race_date=?", (day,)
                    )
                    connection.execute(
                        "DELETE FROM racer_starts WHERE race_date=?", (day,)
                    )

                before = connection.total_changes
                connection.executemany(
                    "INSERT INTO racer_starts "
                    "(race_id,race_date,racer_number) VALUES (?,?,?) "
                    "ON CONFLICT(race_id,racer_number) DO NOTHING",
                    [(s.race_id, s.race_date, s.racer_number) for s in parsed.starts],
                )
                summary.starts_inserted += connection.total_changes - before
                before = connection.total_changes
                connection.executemany(
                    "INSERT INTO accident_events "
                    "(race_id,race_date,racer_number,boat_number,code,is_accident) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(race_id,racer_number) DO NOTHING",
                    [
                        (
                            e.race_id,
                            e.race_date,
                            e.racer_number,
                            e.boat_number,
                            e.code,
                            e.is_accident,
                        )
                        for e in parsed.events
                    ],
                )
                summary.events_inserted += connection.total_changes - before
                if progress is not None and index % 100 == 0:
                    progress(f"processed {index:,}/{len(sources):,} files")
    finally:
        connection.close()
    return summary


def restore_start_timing_history(
    db_path: str | Path,
    raw_dir: str | Path,
    date_from: str,
    date_to: str,
    *,
    rebuild: bool = False,
    seven_zip: str | Path = DEFAULT_7ZIP,
    progress: Callable[[str], None] | None = print,
) -> StartTimingRestoreSummary:
    """Restore only Step 15's new table from the shared official-row parser.

    Ordinary finite ST values are retained for averaging.  Flying values are
    stored as negative observations for auditability but excluded later from
    the average; late/missing values are stored as NULL.  ``--rebuild``
    replaces only dates in ``start_timing_events`` and never touches the Step
    13 accident/start tables.
    """

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if start > end:
        raise ValueError("date_from must not be after date_to")
    sources = _selected_sources(Path(raw_dir), start, end)
    summary = StartTimingRestoreSummary(files_selected=len(sources))
    resolved_db = Path(db_path).resolve()
    if resolved_db.name.lower() == "boatrace.db":
        raise ValueError("refusing to write start timing history to data/boatrace.db")
    connection = sqlite3.connect(resolved_db)
    try:
        with connection:
            ensure_start_timing_schema(connection)
            for index, (source_date, path) in enumerate(sources, 1):
                try:
                    parsed = (
                        parse_official_result_file(path)
                        if path.suffix.lower() == ".txt"
                        else _parse_lzh(path, Path(seven_zip))
                    )
                except Exception as exc:
                    summary.files_skipped += 1
                    message = f"warning: skipped file {path.name}: {exc}"
                    summary.warnings.append(message)
                    if progress is not None:
                        progress(message)
                    continue
                summary.files_parsed += 1
                diag = parsed.diagnostics
                summary.rows_seen += diag.candidate_rows
                summary.fallback_rows += diag.fallback_rows
                summary.rows_skipped += diag.skipped_rows
                summary.incomplete_races += diag.incomplete_races
                summary.events_found += len(parsed.start_timings)
                summary.flying += sum(item.is_flying for item in parsed.start_timings)
                summary.late += sum(item.is_late for item in parsed.start_timings)
                summary.missing += sum(
                    item.start_timing is None for item in parsed.start_timings
                )
                summary.normal_valid += sum(
                    item.start_timing is not None
                    and not item.is_flying
                    and not item.is_late
                    for item in parsed.start_timings
                )
                summary.warnings.extend(f"{path.name}: {item}" for item in diag.warnings)
                if progress is not None:
                    for item in diag.warnings:
                        progress(f"warning: {path.name}: {item}")

                if rebuild:
                    connection.execute(
                        "DELETE FROM start_timing_events WHERE race_date=?",
                        (source_date.isoformat(),),
                    )
                before = connection.total_changes
                connection.executemany(
                    "INSERT INTO start_timing_events "
                    "(race_id,race_date,racer_number,boat_number,course_number,"
                    "start_timing,is_flying,is_late) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(race_id,racer_number) DO NOTHING",
                    [
                        (
                            item.race_id,
                            item.race_date,
                            item.racer_number,
                            item.boat_number,
                            item.course_number,
                            item.start_timing,
                            item.is_flying,
                            item.is_late,
                        )
                        for item in parsed.start_timings
                    ],
                )
                summary.events_inserted += connection.total_changes - before
                if progress is not None and index % 100 == 0:
                    progress(f"processed {index:,}/{len(sources):,} files")
    finally:
        connection.close()
    return summary


def _has_history_tables(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('accident_events','racer_starts')"
    ).fetchall()
    return {str(row[0]) for row in rows} == {"accident_events", "racer_starts"}


def _has_start_timing_table(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='start_timing_events'"
        ).fetchone()
        is not None
    )


def _rows_for_racers(
    conn: sqlite3.Connection,
    sql: str,
    racer_ids: Sequence[int],
    params: Sequence[Any],
) -> Iterator[sqlite3.Row | tuple[Any, ...]]:
    for offset in range(0, len(racer_ids), SQLITE_CHUNK_SIZE):
        chunk = racer_ids[offset : offset + SQLITE_CHUNK_SIZE]
        placeholders = ",".join("?" for _ in chunk)
        yield from conn.execute(sql.format(placeholders=placeholders), (*params, *chunk))


def load_restored_histories(
    conn: sqlite3.Connection,
    period_start: str,
    race_date_exclusive: str,
    racer_ids: Iterable[int],
) -> dict[int, RestoredAccidentHistory]:
    """Load Step 13 rows without including ``race_date_exclusive`` itself."""

    ids = sorted({int(value) for value in racer_ids})
    if not ids or not _has_history_tables(conn):
        return {}
    starts: dict[int, list[str]] = defaultdict(list)
    accidents: dict[int, list[str]] = defaultdict(list)
    for row in _rows_for_racers(
        conn,
        "SELECT racer_number,race_date FROM racer_starts "
        "WHERE race_date>=? AND race_date<? AND racer_number IN ({placeholders}) "
        "ORDER BY racer_number,race_date,race_id",
        ids,
        (period_start, race_date_exclusive),
    ):
        starts[int(row[0])].append(str(row[1]))
    for row in _rows_for_racers(
        conn,
        "SELECT racer_number,race_date FROM accident_events "
        "WHERE is_accident=1 AND race_date>=? AND race_date<? "
        "AND racer_number IN ({placeholders}) ORDER BY racer_number,race_date,race_id",
        ids,
        (period_start, race_date_exclusive),
    ):
        accidents[int(row[0])].append(str(row[1]))
    return {
        racer: RestoredAccidentHistory(
            tuple(starts.get(racer, ())), tuple(accidents.get(racer, ()))
        )
        for racer in set(starts) | set(accidents)
    }


def load_start_timing_histories(
    conn: sqlite3.Connection,
    period_start: str,
    race_date_exclusive: str,
    racer_ids: Iterable[int],
) -> dict[int, RestoredStartTimingHistory]:
    """Load valid ordinary ST only, excluding ``race_date_exclusive`` itself.

    F observations remain queryable in ``start_timing_events`` but are not
    mixed into the mean because no repository evidence establishes that the
    official inspection-period average includes their negative values.
    """

    ids = sorted({int(value) for value in racer_ids})
    if not ids or not _has_start_timing_table(conn):
        return {}
    grouped: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for row in _rows_for_racers(
        conn,
        "SELECT racer_number,race_date,start_timing FROM start_timing_events "
        "WHERE race_date>=? AND race_date<? AND start_timing IS NOT NULL "
        "AND is_flying=0 AND is_late=0 "
        "AND racer_number IN ({placeholders}) "
        "ORDER BY racer_number,race_date,race_id",
        ids,
        (period_start, race_date_exclusive),
    ):
        grouped[int(row[0])].append((str(row[1]), float(row[2])))
    return {
        racer: RestoredStartTimingHistory.from_rows(rows)
        for racer, rows in grouped.items()
    }


def yearly_stats(db_path: str | Path) -> list[dict[str, Any]]:
    """Return restored yearly rows, preserving responsible/non-responsible detail."""

    resolved = Path(db_path).resolve()
    with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True) as conn:
        if not _has_history_tables(conn):
            return []
        rows = conn.execute(
            """SELECT substr(s.race_date,1,4) AS year,
                      COUNT(*) AS starts,
                      COUNT(e.racer_number) AS events,
                      COALESCE(SUM(e.is_accident),0) AS responsible_events
                 FROM racer_starts s
                 LEFT JOIN accident_events e
                   ON e.race_id=s.race_id AND e.racer_number=s.racer_number
                GROUP BY substr(s.race_date,1,4)
                ORDER BY year"""
        ).fetchall()
        return [
            {
                "year": str(row[0]),
                "starts": int(row[1]),
                "events": int(row[2]),
                "responsible_events": int(row[3]),
            }
            for row in rows
        ]


def start_timing_yearly_stats(db_path: str | Path) -> list[dict[str, Any]]:
    """Return yearly measured-ST extraction counts from the Step 15 table."""

    resolved = Path(db_path).resolve()
    with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True) as conn:
        if not _has_start_timing_table(conn):
            return []
        rows = conn.execute(
            """SELECT substr(race_date,1,4) AS year,
                      COUNT(*) AS events,
                      SUM(CASE WHEN start_timing IS NOT NULL
                                AND is_flying=0 AND is_late=0 THEN 1 ELSE 0 END),
                      SUM(is_flying), SUM(is_late),
                      SUM(CASE WHEN start_timing IS NULL THEN 1 ELSE 0 END)
                 FROM start_timing_events
                GROUP BY substr(race_date,1,4)
                ORDER BY year"""
        ).fetchall()
        return [
            {
                "year": str(row[0]),
                "events": int(row[1]),
                "normal_valid": int(row[2] or 0),
                "flying": int(row[3] or 0),
                "late": int(row[4] or 0),
                "missing": int(row[5] or 0),
            }
            for row in rows
        ]
