from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil
import sqlite3

import pytest

from src.features.accident_history import (
    RestoredStartTimingHistory,
    ensure_start_timing_schema,
    load_start_timing_histories,
    parse_official_result_file,
    parse_official_result_text,
    restore_start_timing_history,
    start_timing_yearly_stats,
)
from src.features.asof_builder import _restored_average_start_timing_values


ROOT = Path(__file__).resolve().parents[1]
RAW_RESULTS = ROOT / "data" / "raw" / "results"


def test_shared_result_parser_fixes_start_timing_sign_convention() -> None:
    text = "\n".join(
        [
            "10KBGN",
            " 1R       一般  H1800m",
            "  01  4 4948 選手名   46   36  6.75   4    0.14     1.48.7",
            "  F   3 3211 選手名   55   43  6.67   3   F0.02      .  . ",
            "  L0  1 3600 選手名   42   58  6.73       L .        .  . ",
        ]
    )

    parsed = parse_official_result_text(text, date(2020, 1, 2))
    timings = {item.racer_number: item for item in parsed.start_timings}

    assert timings[4948].start_timing == pytest.approx(0.14)
    assert (timings[4948].is_flying, timings[4948].is_late) == (0, 0)
    assert timings[3211].start_timing == pytest.approx(-0.02)
    assert (timings[3211].is_flying, timings[3211].is_late) == (1, 0)
    assert timings[3600].start_timing is None
    assert (timings[3600].is_flying, timings[3600].is_late) == (0, 1)


def test_real_file_uses_one_shared_row_result_for_accident_and_st() -> None:
    parsed = parse_official_result_file(RAW_RESULTS / "K160613.TXT")
    starts = {(item.race_id, item.racer_number) for item in parsed.starts}
    timings = {(item.race_id, item.racer_number) for item in parsed.start_timings}
    flying = [item for item in parsed.start_timings if item.is_flying]

    assert timings == starts
    assert sorted(item.start_timing for item in flying) == pytest.approx([-0.03, -0.02])
    assert parsed.diagnostics.fallback_rows == 1
    assert parsed.diagnostics.skipped_rows == 0


def test_annual_samples_have_one_st_event_per_start_without_silent_skips() -> None:
    files = sorted(RAW_RESULTS.glob("K??????.TXT"))
    sample_by_year: dict[str, Path] = {}
    for path in files:
        sample_by_year.setdefault(path.stem[1:3], path)

    assert set(sample_by_year) == {f"{year % 100:02d}" for year in range(2016, 2027)}
    for path in sample_by_year.values():
        parsed = parse_official_result_file(path)
        assert len(parsed.start_timings) == len(parsed.starts), path.name
        assert parsed.diagnostics.skipped_rows == 0, path.name
        assert parsed.diagnostics.incomplete_races == 0, path.name


def test_180_day_window_is_exclusive_and_f_is_excluded() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_start_timing_schema(connection)
    connection.executemany(
        "INSERT INTO start_timing_events VALUES (?,?,?,?,?,?,?,?)",
        [
            ("outside", "2024-12-31", 1001, 1, 1, 0.90, 0, 0),
            ("lower", "2025-01-01", 1001, 1, 1, 0.10, 0, 0),
            ("flying", "2025-03-01", 1001, 1, 1, -0.02, 1, 0),
            ("late", "2025-04-01", 1001, 1, None, None, 0, 1),
            ("previous", "2025-06-29", 1001, 1, 1, 0.20, 0, 0),
            ("same-day", "2025-06-30", 1001, 1, 1, 0.80, 0, 0),
            ("future", "2025-07-01", 1001, 1, 1, 0.70, 0, 0),
            ("only-f", "2025-06-01", 1002, 1, 1, -0.03, 1, 0),
        ],
    )
    histories = load_start_timing_histories(
        connection, "2025-01-01", "2025-07-02", [1001, 1002, 1003]
    )

    average, count = _restored_average_start_timing_values(
        histories, "2025-06-30", 1001
    )
    assert count == 2
    assert average == pytest.approx(0.15)
    assert _restored_average_start_timing_values(histories, "2025-06-30", 1002) == (
        None,
        0,
    )
    assert _restored_average_start_timing_values(histories, "2025-06-30", 1003) == (
        None,
        0,
    )


def test_restore_is_append_only_and_rebuild_touches_only_new_table(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copyfile(RAW_RESULTS / "K160613.TXT", raw / "K160613.TXT")
    database = tmp_path / "search.db"

    first = restore_start_timing_history(
        database, raw, "2016-06-13", "2016-06-13", progress=None
    )
    second = restore_start_timing_history(
        database, raw, "2016-06-13", "2016-06-13", progress=None
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO start_timing_events VALUES "
            "('fake','2016-06-13',9999,1,1,0.99,0,0)"
        )
    rebuilt = restore_start_timing_history(
        database,
        raw,
        "2016-06-13",
        "2016-06-13",
        rebuild=True,
        progress=None,
    )

    assert first.events_inserted == first.events_found == 720
    assert (first.normal_valid, first.flying, first.late, first.missing) == (
        717,
        2,
        0,
        1,
    )
    assert second.events_inserted == 0
    assert rebuilt.events_inserted == 720
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM start_timing_events"
        ).fetchone()[0] == 720
        assert connection.execute(
            "SELECT COUNT(*) FROM start_timing_events WHERE race_id='fake'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("start_timing_events",)]
    assert start_timing_yearly_stats(database) == [
        {
            "year": "2016",
            "events": 720,
            "normal_valid": 717,
            "flying": 2,
            "late": 0,
            "missing": 1,
        }
    ]


def test_prefix_sum_history_returns_null_for_zero_valid_starts() -> None:
    history = RestoredStartTimingHistory.from_rows([])
    assert history.average("2025-01-01", "2025-06-30") == (None, 0)
