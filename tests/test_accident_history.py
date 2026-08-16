from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil
import sqlite3

import pytest

from src.features.accident_history import (
    NON_RESPONSIBLE_CODES,
    RESPONSIBLE_CODES,
    RestoredAccidentHistory,
    classify_accident_code,
    parse_official_result_file,
    restore_accident_history,
    yearly_stats,
)
from src.features.asof_builder import (
    _accident_period_start_for_date,
    _restored_period_accident_values,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_RESULTS = ROOT / "data" / "raw" / "results"


def test_known_real_file_recovers_primary_and_fallback_accidents() -> None:
    parsed = parse_official_result_file(RAW_RESULTS / "K160613.TXT")
    events = {event.racer_number: event for event in parsed.events}

    assert (events[4093].code, events[4093].boat_number, events[4093].is_accident) == (
        "F",
        2,
        1,
    )
    assert (events[4253].code, events[4253].boat_number, events[4253].is_accident) == (
        "S1",
        6,
        1,
    )
    assert len(parsed.starts) == parsed.diagnostics.race_count * 6 == 720
    # F rows are normalized and parsed through the existing full-row parser;
    # only the genuinely field-less K cancellation row uses prefix fallback.
    assert parsed.diagnostics.fallback_rows == 1
    assert parsed.diagnostics.skipped_rows == 0
    assert parsed.diagnostics.incomplete_races == 0


@pytest.mark.parametrize("code", sorted(RESPONSIBLE_CODES))
def test_responsible_codes_are_counted(code: str) -> None:
    assert classify_accident_code(code) == 1


@pytest.mark.parametrize("code", [*sorted(NON_RESPONSIBLE_CODES), "ZZ", "01"])
def test_non_responsible_and_unknown_codes_are_not_counted(code: str) -> None:
    assert classify_accident_code(code) == 0


def test_every_year_sample_has_six_starts_per_race_without_silent_skips() -> None:
    files = sorted(RAW_RESULTS.glob("K??????.TXT"))
    sample_by_year: dict[str, Path] = {}
    for path in files:
        sample_by_year.setdefault(path.stem[1:3], path)

    assert set(sample_by_year) == {f"{year % 100:02d}" for year in range(2016, 2027)}
    for path in sample_by_year.values():
        parsed = parse_official_result_file(path)
        assert len(parsed.starts) == parsed.diagnostics.race_count * 6, path.name
        assert len(parsed.start_timings) == len(parsed.starts), path.name
        assert parsed.diagnostics.skipped_rows == 0, path.name
        assert parsed.diagnostics.incomplete_races == 0, path.name


def test_period_values_exclude_race_day_and_later_events() -> None:
    history = RestoredAccidentHistory(
        start_dates=("2025-05-01", "2025-05-10", "2025-05-11", "2025-05-12"),
        accident_dates=("2025-05-10", "2025-05-11", "2025-05-12"),
    )

    rate, accidents, starts = history.period_values("2025-05-01", "2025-05-11")

    assert (accidents, starts) == (1, 2)
    assert rate == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("race_date", "expected"),
    [
        ("2025-04-30", "2024-11-01"),
        ("2025-05-01", "2025-05-01"),
        ("2025-10-31", "2025-05-01"),
        ("2025-11-01", "2025-11-01"),
    ],
)
def test_assessment_period_boundaries(race_date: str, expected: str) -> None:
    assert _accident_period_start_for_date(race_date) == expected


def test_zero_starts_returns_null_rate() -> None:
    history = RestoredAccidentHistory((), ())
    assert history.period_values("2025-05-01", "2025-06-01") == (None, 0, 0)
    assert _restored_period_accident_values({}, "2025-06-01", 4320) == (None, 0, 0)


def test_unknown_00_rank_is_preserved_but_never_counted() -> None:
    parsed = parse_official_result_file(RAW_RESULTS / "K171109.TXT")
    unknown = [event for event in parsed.events if event.code == "00"]

    assert len(unknown) == 1
    assert unknown[0].is_accident == 0
    assert parsed.diagnostics.unknown_codes == {"00": 1}


def test_restore_refuses_boatrace_database_even_for_a_temporary_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="refusing"):
        restore_accident_history(
            tmp_path / "boatrace.db",
            RAW_RESULTS,
            "2016-06-13",
            "2016-06-13",
            progress=None,
        )


def test_restore_is_append_only_and_rebuild_replaces_only_requested_period(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copyfile(RAW_RESULTS / "K160613.TXT", raw / "K160613.TXT")
    database = tmp_path / "search.db"

    first = restore_accident_history(
        database, raw, "2016-06-13", "2016-06-13", progress=None
    )
    second = restore_accident_history(
        database, raw, "2016-06-13", "2016-06-13", progress=None
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO racer_starts VALUES ('fake','2016-06-13',9999)"
        )
    rebuilt = restore_accident_history(
        database,
        raw,
        "2016-06-13",
        "2016-06-13",
        rebuild=True,
        progress=None,
    )

    assert first.starts_inserted == 720
    assert first.events_inserted == first.events_found
    assert second.starts_inserted == second.events_inserted == 0
    assert rebuilt.starts_inserted == 720
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM racer_starts").fetchone()[0] == 720
        assert connection.execute(
            "SELECT COUNT(*) FROM racer_starts WHERE race_id='fake'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0] == 2
    assert yearly_stats(database) == [
        {
            "year": "2016",
            "starts": 720,
            "events": first.events_found,
            "responsible_events": first.responsible_events,
        }
    ]
