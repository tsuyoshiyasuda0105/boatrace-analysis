from __future__ import annotations

from datetime import date, timedelta
import io
import sqlite3

import pytest

from src.features.asof_builder import (
    build_features,
    coverage_rows,
    exhibition_metrics,
    verify_features,
)


def _source() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY, race_date TEXT NOT NULL,
          stadium_number INTEGER NOT NULL, race_number INTEGER NOT NULL,
          race_grade_number INTEGER, race_title TEXT, race_subtitle TEXT,
          race_distance INTEGER, race_closed_at TEXT, series_day INTEGER,
          is_yusho INTEGER DEFAULT 0, is_jun_yusho INTEGER DEFAULT 0
        );
        CREATE TABLE race_entries (
          race_id TEXT, boat_number INTEGER, racer_number INTEGER,
          racer_name TEXT, class_number INTEGER, branch_number INTEGER,
          birthplace_number INTEGER, age INTEGER, weight REAL,
          flying_count INTEGER, late_count INTEGER, avg_start_timing REAL,
          national_top_1_percent REAL, national_top_2_percent REAL,
          national_top_3_percent REAL, local_top_1_percent REAL,
          local_top_2_percent REAL, local_top_3_percent REAL,
          assigned_motor_number INTEGER, assigned_motor_top_2_percent REAL,
          assigned_motor_top_3_percent REAL, assigned_boat_number INTEGER,
          assigned_boat_top_2_percent REAL, assigned_boat_top_3_percent REAL,
          PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE racers (
          racer_number INTEGER PRIMARY KEY, name TEXT, name_kana TEXT,
          branch_number INTEGER, birthplace_number INTEGER, birth_date TEXT,
          gender INTEGER, height_cm INTEGER, blood_type TEXT,
          registered_period INTEGER, updated_at TEXT
        );
        CREATE TABLE race_previews (
          race_id TEXT, boat_number INTEGER, weather_number INTEGER,
          wind_speed INTEGER, wind_direction_number INTEGER, wave_height INTEGER,
          temperature REAL, water_temperature REAL, course_number INTEGER,
          exhibition_time REAL, start_timing_exhibition REAL,
          weight_adjustment REAL, tilt_adjustment REAL, live_updated_at TEXT,
          stable_plate INTEGER, PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE race_results (
          race_id TEXT, boat_number INTEGER, finishing_position INTEGER,
          course_number INTEGER, start_timing REAL, race_time TEXT,
          remarks TEXT, kimarite TEXT, PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE race_payouts (
          race_id TEXT, bet_type TEXT, combination TEXT, payout INTEGER,
          popularity INTEGER, PRIMARY KEY (race_id, bet_type, combination)
        );
        CREATE TABLE race_tides (
          race_id TEXT PRIMARY KEY, stadium_number INTEGER, tide_station TEXT,
          race_time TEXT, tide_height_cm REAL, tide_phase TEXT,
          nearest_high_time TEXT, nearest_high_cm REAL,
          nearest_low_time TEXT, nearest_low_cm REAL,
          minutes_from_high INTEGER, minutes_from_low INTEGER,
          tide_range_cm REAL, tide_delta_60m_cm REAL,
          is_high_tide_zone INTEGER, is_low_tide_zone INTEGER,
          source TEXT, fetched_at TEXT
        );
        """
    )
    return conn


def _race(conn: sqlite3.Connection, race_id: str, race_date: str, race_no: int = 1) -> None:
    conn.execute(
        "INSERT INTO races VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            race_id,
            race_date,
            5,
            race_no,
            3,
            "合成テスト開催",
            None,
            1800,
            f"{race_date} 09:{10 + race_no:02d}:00",
            None,
            0,
            0,
        ),
    )


def _entry(
    conn: sqlite3.Connection,
    race_id: str,
    boat: int,
    racer: int,
    class_number: int = 3,
) -> None:
    conn.execute(
        """INSERT INTO race_entries VALUES (
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            race_id,
            boat,
            racer,
            f"racer-{racer}",
            class_number,
            1,
            1,
            30,
            50.0,
            0,
            0,
            0.10 + boat / 100,
            10.0 + boat,
            50.0 + boat,
            70.0 + boat,
            20.0 + boat,
            40.0 + boat,
            60.0 + boat,
            10 + boat,
            30.0 + boat,
            50.0 + boat,
            20 + boat,
            25.0 + boat,
            45.0 + boat,
        ),
    )


def _result(
    conn: sqlite3.Connection,
    race_id: str,
    boat: int,
    position: int | None,
    *,
    kimarite: str | None = None,
    remarks: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO race_results VALUES (?,?,?,?,?,?,?,?)",
        (race_id, boat, position, boat, 0.15, None, remarks, kimarite),
    )


def _complete_fixture() -> sqlite3.Connection:
    conn = _source()
    # Exactly outside, at the inclusive lower boundary, at asof_date, target
    # day, and a future day.  Only the boundary and asof rows may contribute.
    for race_id, race_date in [
        ("old-out", "2024-06-01"),
        ("old-in", "2024-06-02"),
        ("asof", "2025-06-01"),
        ("target", "2025-06-02"),
        ("future", "2025-06-03"),
    ]:
        _race(conn, race_id, race_date)
    _entry(conn, "old-out", 1, 1001)
    _entry(conn, "old-in", 1, 1001)
    _entry(conn, "asof", 1, 1001)
    _entry(conn, "future", 1, 1001)
    _result(conn, "old-out", 1, 1, kimarite="逃げ")
    _result(conn, "old-in", 1, 1, kimarite="逃げ")
    _result(conn, "asof", 1, None, remarks="S0")
    _result(conn, "future", 1, 1, kimarite="まくり")

    classes = {1: 2, 2: 1, 3: 3, 4: 3, 5: 4, 6: 3}
    times = {1: 6.70, 2: 6.80, 3: 6.80, 4: 7.00, 5: 7.10, 6: 7.20}
    for boat in range(1, 7):
        racer = 1000 + boat
        conn.execute(
            "INSERT OR IGNORE INTO racers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (racer, f"racer-{racer}", None, 1, 1, None, 2 if boat == 6 else 1, None, None, None, "2025-01-01"),
        )
        _entry(conn, "target", boat, racer, classes[boat])
        conn.execute(
            "INSERT INTO race_previews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "target",
                boat,
                1,
                4,
                9,
                2,
                20.0,
                19.0,
                boat,
                times[boat],
                -0.02 if boat == 1 else 0.10 + boat / 100,
                0.0,
                0.0,
                None,
                0,
            ),
        )
        _result(conn, "target", boat, boat, kimarite="逃げ" if boat == 1 else None)
    conn.execute(
        "INSERT INTO race_tides VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("target", 5, "x", "2025-06-02 09:11:00", None, "rising", None, None, None, None, None, None, None, None, 0, 0, "fixture", None),
    )
    conn.executemany(
        "INSERT INTO race_payouts VALUES (?,?,?,?,?)",
        [
            ("target", "trifecta", "1-2-3", 1230, 1),
            ("target", "exacta", "1-2", 450, 1),
            ("target", "win", "1", 120, 1),
        ],
    )
    conn.commit()
    return conn


def _read_row(path, race_id="target"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM asof_race_features WHERE race_id=?", (race_id,)
    ).fetchone()
    conn.close()
    return row


def test_future_cutoff_boundaries_and_verify(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    result = build_features(
        source, output, "2025-06-02", "2025-06-02", built_at="fixed"
    )
    assert result == {"selected": 1, "inserted": 1, "skipped_existing": 0, "warnings": 0}
    row = _read_row(output)
    assert row["asof_date"] == "2025-06-01"
    assert row["b1_kimarite_rate_nige"] == pytest.approx(50.0)
    assert row["b1_kimarite_rate_makuri"] == pytest.approx(0.0)
    assert row["b1_accident_rate"] == pytest.approx(50.0)
    checked = verify_features(
        source, output, 20, date_from="2025-06-02", date_to="2025-06-02"
    )
    assert checked == {
        "ok": True,
        "rows": 1,
        "sampled": 1,
        "chronology_errors": 0,
        "mismatches": [],
    }


def test_future_insert_does_not_change_rebuilt_past_aggregates(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    # The pre-existing future row is removed, then added only after first build.
    source.execute("DELETE FROM race_results WHERE race_id='future'")
    source.execute("DELETE FROM race_entries WHERE race_id='future'")
    source.execute("DELETE FROM races WHERE race_id='future'")
    source.commit()
    build_features(source, output, "2025-06-02", "2025-06-02", built_at="fixed")
    before = tuple(
        _read_row(output)[name]
        for name in ("b1_kimarite_rate_nige", "b1_kimarite_rate_makuri", "b1_accident_rate")
    )
    _race(source, "future-new", "2025-06-03")
    _entry(source, "future-new", 1, 1001)
    _result(source, "future-new", 1, 1, kimarite="まくり")
    source.commit()
    build_features(
        source,
        output,
        "2025-06-02",
        "2025-06-02",
        rebuild=True,
        built_at="fixed",
    )
    after = tuple(
        _read_row(output)[name]
        for name in ("b1_kimarite_rate_nige", "b1_kimarite_rate_makuri", "b1_accident_rate")
    )
    assert after == before


def test_program_and_preview_values_are_copied_and_metrics_are_correct(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    build_features(source, output, "2025-06-02", "2025-06-02")
    row = _read_row(output)
    assert row["b1_avg_st"] == pytest.approx(0.11)
    assert row["b1_national_rate"] == pytest.approx(11.0)
    assert row["b1_local_rate"] == pytest.approx(21.0)
    assert row["b1_motor_rate2"] == pytest.approx(31.0)
    assert row["b1_ex_time"] == pytest.approx(6.70)
    assert row["b1_ex_st"] == pytest.approx(-0.02)
    assert [row[f"b{boat}_ex_rank"] for boat in range(1, 7)] == [1, 2, 2, 4, 5, 6]
    assert row["b1_ex_dev"] == pytest.approx(6.70 - (6.70 + 6.80 + 6.80 + 7.00 + 7.10 + 7.20) / 6)
    assert row["b6_ex_dev"] == pytest.approx(7.20 - (6.70 + 6.80 + 6.80 + 7.00 + 7.10 + 7.20) / 6)


def test_class_gender_conditions_and_three_payout_types(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    build_features(source, output, "2025-06-02", "2025-06-02")
    row = _read_row(output)
    assert row["class_mix"] == "A1単騎"
    assert row["female_present"] == 1
    assert row["result_sanrentan"] == "1-2-3"
    assert row["payout_sanrentan"] == 1230
    assert row["result_nirentan"] == "1-2"
    assert row["payout_nirentan"] == 450
    assert row["result_tansho"] == 1
    assert row["payout_tansho"] == 120
    assert row["weather"] == "晴"
    assert row["wind_dir"] is None
    assert row["wind_dir_raw"] == 9
    assert row["tide_phase"] == "上げ潮"


def test_append_only_rerun_skips_and_preserves_row(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    build_features(source, output, "2025-06-02", "2025-06-02", built_at="first")
    source.execute(
        "UPDATE race_entries SET avg_start_timing=0.99 WHERE race_id='target' AND boat_number=1"
    )
    source.commit()
    second = build_features(source, output, "2025-06-02", "2025-06-02", built_at="second")
    row = _read_row(output)
    assert second == {"selected": 1, "inserted": 0, "skipped_existing": 1, "warnings": 0}
    assert row["b1_avg_st"] == pytest.approx(0.11)
    assert row["built_at"] == "first"


def test_large_backfill_chunks_sql_variables_and_remains_append_only(tmp_path):
    source = _source()
    first_date = date(2020, 1, 1)
    race_count = 1001
    for index in range(race_count):
        race_date = (first_date + timedelta(days=index)).isoformat()
        race_id = f"large-{index:04d}"
        _race(source, race_id, race_date)
        _entry(source, race_id, 1, 10000 + index)
    source.commit()
    source.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    output = tmp_path / "features.db"
    date_to = (first_date + timedelta(days=race_count - 1)).isoformat()
    first = build_features(
        source,
        output,
        first_date.isoformat(),
        date_to,
        built_at="first",
        progress_stream=io.StringIO(),
    )
    second = build_features(
        source,
        output,
        first_date.isoformat(),
        date_to,
        built_at="second",
        progress_stream=io.StringIO(),
    )

    assert first == {
        "selected": race_count,
        "inserted": race_count,
        "skipped_existing": 0,
        "warnings": 0,
    }
    assert second == {
        "selected": race_count,
        "inserted": 0,
        "skipped_existing": race_count,
        "warnings": 0,
    }
    assert _read_row(output, "large-0000")["built_at"] == "first"
    assert _read_row(output, "large-1000")["b1_racer_id"] == 11000


def test_missing_exhibition_keeps_nulls_but_creates_row(tmp_path):
    source = _complete_fixture()
    _race(source, "no-preview", "2025-06-04")
    for boat in range(1, 7):
        _entry(source, "no-preview", boat, 2000 + boat)
    source.commit()
    output = tmp_path / "features.db"
    result = build_features(source, output, "2025-06-04", "2025-06-04")
    row = _read_row(output, "no-preview")
    assert result["inserted"] == 1
    for boat in range(1, 7):
        assert row[f"b{boat}_ex_time"] is None
        assert row[f"b{boat}_ex_rank"] is None
        assert row[f"b{boat}_ex_dev"] is None
        assert row[f"b{boat}_ex_st"] is None


def test_exhibition_derivation_requires_all_six_and_coverage_reports(tmp_path):
    metrics = exhibition_metrics({1: 6.7, 2: 6.8, 3: 6.9, 4: 7.0, 5: 7.1, 6: None})
    assert metrics == {boat: (None, None) for boat in range(1, 7)}
    source = _complete_fixture()
    output = tmp_path / "features.db"
    build_features(source, output, "2025-06-02", "2025-06-02", progress_stream=io.StringIO())
    coverage = {row["column"]: row for row in coverage_rows(output)}
    assert coverage["race_id"]["coverage_pct"] == 100.0
    assert coverage["b1_ex_time"]["oldest_date"] == "2025-06-02"
    assert coverage["wind_dir"]["coverage_pct"] == 0.0
