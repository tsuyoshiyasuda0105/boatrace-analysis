from __future__ import annotations

from datetime import date, timedelta
import io
import json
import sqlite3

import pytest

from src.features.accident_history import (
    ensure_accident_history_schema,
    ensure_start_timing_schema,
)
from src.features.asof_builder import (
    ALL_COLUMNS,
    build_features,
    coverage_rows,
    create_output_schema,
    exhibition_metrics,
    load_stadium_orientations,
    relative_wind_direction,
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
        CREATE TABLE racer_accident_period_stats (
          racer_number INTEGER, period_start TEXT, period_end TEXT,
          source_kind TEXT, rule_version TEXT, accident_rate REAL,
          accident_points REAL
        );
        CREATE TABLE odds_trifecta (
          race_id TEXT, combination TEXT, odds REAL, is_final INTEGER,
          recorded_at TEXT, snapshot_label TEXT
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
            (
                racer,
                f"racer-{racer}",
                None,
                1,
                1,
                "2000-06-02" if boat == 1 else "2000-06-03",
                2 if boat == 6 else 1,
                None,
                None,
                None,
                "2025-01-01",
            ),
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
    conn.executemany(
        "INSERT INTO racer_accident_period_stats VALUES (?,?,?,?,?,?,?)",
        [
            (1001, "2025-05-01", "2025-05-31", "reconstructed", "official_table_2025_05_reconstructed_v2", 0.40, 2),
            (1002, "2025-05-01", "2025-05-31", "reconstructed", "official_table_2025_05_reconstructed_v2", 0.90, 9),
            (1001, "2025-05-01", "2025-06-01", "reconstructed", "official_table_2025_05_reconstructed_v2", 0.55, 3),
            (1001, "2025-05-01", "2025-06-01", "other", "official_table_2025_05_reconstructed_v2", 9.99, 99),
            (1001, "2025-05-01", "2025-06-01", "reconstructed", "other-rule", 8.88, 88),
            (1001, "2025-05-01", "2025-06-02", "reconstructed", "official_table_2025_05_reconstructed_v2", 7.77, 77),
        ],
    )
    conn.executemany(
        "INSERT INTO odds_trifecta VALUES (?,?,?,?,?,?)",
        [
            ("target", "1-2-3", 9.0, 0, "2025-06-02T09:00:00", "T-5min"),
            ("target", "1-2-3", 8.0, 0, "2025-06-02T09:01:00", "T-5min"),
            ("target", "1-3-2", 5.5, 0, "2025-06-02T09:01:00", "T-5min"),
            ("target", "2-1-3", 0.0, 0, "2025-06-02T09:01:00", "T-5min"),
            ("target", "1-3-2", 2.0, 1, "2025-06-02T09:10:00", "final"),
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


def _course_rate_fixture(
    output,
    *,
    prior_courses: list[int],
    winning_indexes: set[int],
    kimarite: str = "逃げ",
    target_boat: int = 1,
) -> sqlite3.Connection:
    source = _source()
    events = []
    for index, course in enumerate(prior_courses, 1):
        race_id = f"history-{index}"
        race_date = f"2025-05-{index:02d}"
        _race(source, race_id, race_date)
        _entry(source, race_id, 1, 1001, class_number=1)
        _result(
            source,
            race_id,
            1,
            1 if index in winning_indexes else 2,
            kimarite=kimarite if index in winning_indexes else None,
        )
        events.append((race_id, race_date, 1001, 1, course, 0.15, 0, 0))
    _race(source, "target", "2025-06-02")
    _entry(source, "target", target_boat, 1001, class_number=1)
    source.commit()
    with sqlite3.connect(output) as connection:
        create_output_schema(connection)
        ensure_start_timing_schema(connection)
        connection.executemany(
            "INSERT INTO start_timing_events VALUES (?,?,?,?,?,?,?,?)", events
        )
    return source


def test_boat1_nige_rate_is_unchanged_from_schema_v8_definition(tmp_path):
    output = tmp_path / "course-denominator.db"
    source = _course_rate_fixture(
        output,
        prior_courses=[1, 1, 1, 1, 1, 2, 2, 2, 2, 2],
        winning_indexes={1, 2, 3},
    )

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    # Schema v8's nige denominator was also course 1 only: 3 / 5 = 60%.
    assert row["b1_kimarite_rate_nige"] == pytest.approx(60.0)


def test_boat2_sashi_rate_uses_only_course2_entries(tmp_path):
    output = tmp_path / "attack-course-denominator.db"
    source = _course_rate_fixture(
        output,
        prior_courses=[2] * 10 + [3] * 5,
        winning_indexes={1, 2, 3, 11, 12, 13, 14, 15},
        kimarite="差し",
        target_boat=2,
    )

    build_features(source, output, "2025-06-02", "2025-06-02")

    # Course 3's five wins must not dilute or inflate boat 2's 3 / 10 rate.
    assert _read_row(output)["b2_kimarite_rate_sashi"] == pytest.approx(30.0)


def test_boat_course_without_entries_has_null_kimarite_rates(tmp_path):
    output = tmp_path / "no-matching-course.db"
    source = _course_rate_fixture(
        output,
        prior_courses=[3, 3, 3, 3, 3],
        winning_indexes={1, 2, 3},
        kimarite="差し",
        target_boat=2,
    )

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert row["b2_kimarite_rate_sashi"] is None
    assert row["b2_kimarite_rate_makuri"] is None


def test_kimarite_rate_excludes_target_day_and_future_events(tmp_path):
    output = tmp_path / "course-asof.db"
    source = _course_rate_fixture(
        output,
        prior_courses=[1, 1, 1, 1, 1],
        winning_indexes={1, 2, 3},
    )
    with sqlite3.connect(output) as connection:
        connection.executemany(
            "INSERT INTO start_timing_events VALUES (?,?,?,?,?,?,?,?)",
            [
                ("same-day", "2025-06-02", 1001, 1, 1, 0.10, 0, 0),
                ("future-course", "2025-06-03", 1001, 1, 1, 0.10, 0, 0),
            ],
        )
    for race_id, race_date in (
        ("same-day", "2025-06-02"),
        ("future-course", "2025-06-03"),
    ):
        _race(source, race_id, race_date)
        _entry(source, race_id, 1, 1001, class_number=1)
        _result(source, race_id, 1, 1, kimarite="逃げ")
    source.commit()

    build_features(source, output, "2025-06-02", "2025-06-02")

    assert _read_row(output)["b1_kimarite_rate_nige"] == pytest.approx(60.0)
    assert verify_features(
        source, output, sample=1, date_from="2025-06-02", date_to="2025-06-02"
    )["ok"] is True


def test_kimarite_rate_is_null_below_entry_threshold_or_without_entries(tmp_path):
    output = tmp_path / "course-threshold.db"
    source = _course_rate_fixture(
        output,
        prior_courses=[1, 1, 1, 1],
        winning_indexes={1, 2, 3},
    )
    _entry(source, "target", 2, 1002, class_number=1)
    source.commit()

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert row["b1_kimarite_rate_nige"] is None
    assert row["b2_kimarite_rate_nige"] is None


def test_future_cutoff_boundaries_and_verify(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    result = build_features(
        source, output, "2025-06-02", "2025-06-02", built_at="fixed"
    )
    assert result == {"selected": 1, "inserted": 1, "skipped_existing": 0, "warnings": 0}
    row = _read_row(output)
    assert row["asof_date"] == "2025-06-01"
    assert row["b1_kimarite_rate_nige"] is None
    assert row["b1_kimarite_rate_makuri"] is None
    assert row["b1_accident_rate"] == pytest.approx(0.55)
    assert row["b1_accident_points"] == pytest.approx(3.0)
    assert row["b1_accident_source"] == "period"
    assert row["b2_accident_rate"] == pytest.approx(0.0)
    assert row["b2_accident_source"] == "missing_zero"
    assert row["b1_accident_rate_365d"] == pytest.approx(50.0)
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
        for name in ("b1_kimarite_rate_nige", "b1_kimarite_rate_makuri", "b1_accident_rate_365d")
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
        for name in ("b1_kimarite_rate_nige", "b1_kimarite_rate_makuri", "b1_accident_rate_365d")
    )
    assert after == before


def test_program_and_preview_values_are_copied_and_metrics_are_correct(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "features.db"
    build_features(source, output, "2025-06-02", "2025-06-02")
    row = _read_row(output)
    assert row["b1_avg_st"] is None
    assert row["b1_avg_st_n"] == 0
    assert row["b1_avg_st_official"] == pytest.approx(0.11)
    assert row["b1_national_rate"] == pytest.approx(11.0)
    assert row["b1_local_rate"] == pytest.approx(21.0)
    assert row["b1_national_rate2"] == pytest.approx(51.0)
    assert row["b1_local_rate2"] == pytest.approx(41.0)
    assert row["b1_age"] == 25
    assert row["b2_age"] == 24
    assert row["schema_version"] == 9
    assert row["b1_motor_rate2"] == pytest.approx(31.0)
    assert row["b1_ex_time"] == pytest.approx(6.70)
    assert row["b1_ex_st"] == pytest.approx(-0.02)
    assert [row[f"b{boat}_ex_rank"] for boat in range(1, 7)] == [1, 2, 2, 4, 5, 6]
    assert row["b1_ex_dev"] == pytest.approx(6.70 - (6.70 + 6.80 + 6.80 + 7.00 + 7.10 + 7.20) / 6)
    assert row["b6_ex_dev"] == pytest.approx(7.20 - (6.70 + 6.80 + 6.80 + 7.00 + 7.10 + 7.20) / 6)
    assert row["t5_odds_favorite"] == pytest.approx(5.5)


def test_missing_or_invalid_birth_date_keeps_age_null(tmp_path):
    source = _complete_fixture()
    source.execute("UPDATE racers SET birth_date=NULL WHERE racer_number=1001")
    source.execute("UPDATE racers SET birth_date='invalid' WHERE racer_number=1002")
    output = tmp_path / "features.db"

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert row["b1_age"] is None
    assert row["b2_age"] is None


def test_schema_v2_rows_are_additively_migrated_and_preserved(tmp_path):
    output = tmp_path / "legacy-v2.db"
    new_suffixes = (
        "_age",
        "_national_rate2",
        "_local_rate2",
        "_avg_st_n",
        "_avg_st_official",
    )
    legacy_columns = [
        (name, kind)
        for name, kind in ALL_COLUMNS
        if not name.endswith(new_suffixes) and not name.endswith("_json")
    ]
    with sqlite3.connect(output) as connection:
        ddl = ", ".join(f"{name} {kind}" for name, kind in legacy_columns)
        connection.execute(f"CREATE TABLE asof_race_features ({ddl})")
        connection.execute(
            "INSERT INTO asof_race_features "
            "(race_id,race_date,asof_date,built_at,schema_version) VALUES (?,?,?,?,?)",
            ("legacy", "2025-01-01", "2024-12-31", "fixed", 2),
        )
        create_output_schema(connection)
        row = connection.execute(
            "SELECT schema_version,b1_age,b1_national_rate2,b1_local_rate2,"
            "b1_avg_st_n,b1_avg_st_official,result_tansho_json,payout_tansho_json "
            "FROM asof_race_features WHERE race_id='legacy'"
        ).fetchone()

    assert row == (2, None, None, None, 0, None, None, None)


def test_schema_v4_accident_rate_is_moved_to_365d_before_name_reuse(tmp_path):
    output = tmp_path / "legacy-v4.db"
    with sqlite3.connect(output) as connection:
        connection.execute(
            "CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, "
            "race_date TEXT, asof_date TEXT, built_at TEXT, schema_version INTEGER, "
            "b1_accident_rate REAL)"
        )
        connection.execute(
            "INSERT INTO asof_race_features VALUES ('legacy','2025-01-01',"
            "'2024-12-31','fixed',4,37.5)"
        )
        create_output_schema(connection)
        row = connection.execute(
            "SELECT b1_accident_rate,b1_accident_rate_365d "
            "FROM asof_race_features WHERE race_id='legacy'"
        ).fetchone()
    assert row == (None, 37.5)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, "向かい風"), (3, "向かい風"), (5, "横風(右)"), (7, "追い風"), (9, "追い風"), (13, "横風(左)"), (15, "向かい風")],
)
def test_relative_wind_direction_includes_45_and_135_degree_boundaries(raw, expected):
    assert relative_wind_direction(5, raw, 3, orientations={5: 0.0}) == expected


def test_relative_wind_direction_handles_calm_and_unknown_orientation():
    assert relative_wind_direction(5, 17, 3, orientations={5: None}) == "無風"
    assert relative_wind_direction(5, 1, 0, orientations={5: None}) == "無風"
    assert relative_wind_direction(5, 1, 3, orientations={5: None}) is None


def test_orientation_master_covers_every_venue_without_invented_headings():
    orientations = load_stadium_orientations()
    assert set(orientations) == set(range(1, 25))
    assert all(value is None for value in orientations.values())


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
    assert row["result_tansho_json"] == '["1"]'
    assert row["payout_tansho_json"] == '{"1":120}'
    assert row["result_nirentan_json"] == '["1-2"]'
    assert row["payout_nirentan_json"] == '{"1-2":450}'
    assert row["result_sanrentan_json"] == '["1-2-3"]'
    assert row["payout_sanrentan_json"] == '{"1-2-3":1230}'
    assert row["weather"] == "晴"
    assert row["wind_dir"] is None
    assert row["wind_dir_raw"] == 9
    assert row["tide_phase"] == "上げ潮"


def test_results_come_from_finish_order_and_payout_is_matched_by_combination(tmp_path):
    source = _complete_fixture()
    source.executemany(
        "INSERT INTO race_payouts VALUES (?,?,?,?,?)",
        [
            ("target", "win", "3", 999, 9),
            ("target", "exacta", "3-2", 9999, 9),
            ("target", "trifecta", "3-2-1", 99999, 9),
        ],
    )
    output = tmp_path / "features.db"

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert (row["result_tansho"], row["payout_tansho"]) == (1, 120)
    assert (row["result_nirentan"], row["payout_nirentan"]) == ("1-2", 450)
    assert (row["result_sanrentan"], row["payout_sanrentan"]) == ("1-2-3", 1230)


def test_dead_heat_preserves_all_winning_combinations_and_payouts(tmp_path):
    source = _complete_fixture()
    source.execute(
        "UPDATE race_results SET finishing_position=1 WHERE race_id='target' AND boat_number=2"
    )
    source.execute(
        "UPDATE race_results SET finishing_position=3 WHERE race_id='target' AND boat_number=3"
    )
    source.execute("DELETE FROM race_payouts WHERE race_id='target'")
    source.executemany(
        "INSERT INTO race_payouts VALUES (?,?,?,?,?)",
        [
            ("target", "win", "1", 130, 1),
            ("target", "win", "2", 380, 2),
            ("target", "exacta", "1-2", 190, 1),
            ("target", "exacta", "2-1", 520, 2),
            ("target", "trifecta", "1-2-3", 780, 1),
            ("target", "trifecta", "2-1-3", 2430, 2),
        ],
    )
    output = tmp_path / "dead-heat.db"

    result = build_features(source, output, "2025-06-02", "2025-06-02")

    assert result["warnings"] == 0
    row = _read_row(output)
    assert json.loads(row["result_tansho_json"]) == ["1", "2"]
    assert json.loads(row["payout_tansho_json"]) == {"1": 130, "2": 380}
    assert json.loads(row["result_nirentan_json"]) == ["1-2", "2-1"]
    assert json.loads(row["payout_nirentan_json"]) == {"1-2": 190, "2-1": 520}
    assert json.loads(row["result_sanrentan_json"]) == ["1-2-3", "2-1-3"]
    assert json.loads(row["payout_sanrentan_json"]) == {
        "1-2-3": 780,
        "2-1-3": 2430,
    }


def test_missing_or_duplicate_matching_payout_nulls_only_that_bet_type(tmp_path):
    source = _complete_fixture()
    source.execute(
        "INSERT INTO race_payouts VALUES (?,?,?,?,?)",
        ("target", "exacta", "1－2", 451, 2),
    )
    source.execute(
        "DELETE FROM race_payouts WHERE race_id='target' AND bet_type='trifecta'"
    )
    output = tmp_path / "invalid-payouts.db"
    stream = io.StringIO()

    result = build_features(
        source, output, "2025-06-02", "2025-06-02", progress_stream=stream
    )

    row = _read_row(output)
    assert result["warnings"] == 2
    assert row["result_tansho"] == 1
    assert row["result_nirentan"] is None
    assert row["payout_nirentan_json"] is None
    assert row["result_sanrentan"] is None
    assert "found 2" in stream.getvalue()
    assert "found 0" in stream.getvalue()


def test_history_ignores_nonwinner_kimarite_and_numeric_finish_accident(tmp_path):
    source = _complete_fixture()
    source.execute(
        "UPDATE race_results SET finishing_position=2, kimarite='逃げ' WHERE race_id='old-in'"
    )
    source.execute(
        "UPDATE race_results SET finishing_position=1, remarks='S0' WHERE race_id='asof'"
    )
    output = tmp_path / "guarded-history.db"

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert row["b1_kimarite_rate_nige"] is None
    assert row["b1_accident_rate"] == pytest.approx(0.55)
    assert row["b1_accident_rate_365d"] == pytest.approx(0.0)


def test_restored_period_accidents_are_cut_off_before_race_day(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "restored-accidents.db"
    with sqlite3.connect(output) as connection:
        create_output_schema(connection)
        ensure_accident_history_schema(connection)
        connection.executemany(
            "INSERT INTO racer_starts VALUES (?,?,?)",
            [
                ("before-1", "2025-05-01", 1001),
                ("before-2", "2025-06-01", 1001),
                ("same-day", "2025-06-02", 1001),
                ("future", "2025-06-03", 1001),
            ],
        )
        connection.executemany(
            "INSERT INTO accident_events VALUES (?,?,?,?,?,?)",
            [
                ("before-2", "2025-06-01", 1001, 1, "F", 1),
                ("same-day", "2025-06-02", 1001, 1, "S1", 1),
                ("future", "2025-06-03", 1001, 1, "S2", 1),
            ],
        )

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert row["b1_accident_count_period"] == 1
    assert row["b1_starts_period"] == 2
    assert row["b1_accident_rate_period"] == pytest.approx(50.0)
    assert row["b2_accident_count_period"] == 0
    assert row["b2_starts_period"] == 0
    assert row["b2_accident_rate_period"] is None
    assert verify_features(source, output, sample=1)["ok"] is True


def test_restored_average_st_uses_previous_180_days_only(tmp_path):
    source = _complete_fixture()
    output = tmp_path / "restored-start-timing.db"
    with sqlite3.connect(output) as connection:
        create_output_schema(connection)
        ensure_start_timing_schema(connection)
        connection.executemany(
            "INSERT INTO start_timing_events VALUES (?,?,?,?,?,?,?,?)",
            [
                ("outside", "2024-12-03", 1001, 1, 1, 0.90, 0, 0),
                ("lower", "2024-12-04", 1001, 1, 1, 0.10, 0, 0),
                ("flying", "2025-05-01", 1001, 1, 1, -0.02, 1, 0),
                ("late", "2025-05-02", 1001, 1, None, None, 0, 1),
                ("previous", "2025-06-01", 1001, 1, 1, 0.20, 0, 0),
                ("same-day", "2025-06-02", 1001, 1, 1, 0.80, 0, 0),
                ("future", "2025-06-03", 1001, 1, 1, 0.70, 0, 0),
                ("only-f", "2025-05-01", 1002, 2, 2, -0.03, 1, 0),
            ],
        )

    build_features(source, output, "2025-06-02", "2025-06-02")

    row = _read_row(output)
    assert row["b1_avg_st"] == pytest.approx(0.15)
    assert row["b1_avg_st_n"] == 2
    assert row["b1_avg_st_official"] == pytest.approx(0.11)
    assert row["b2_avg_st"] is None
    assert row["b2_avg_st_n"] == 0
    assert verify_features(source, output, sample=1)["ok"] is True


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
    assert row["b1_avg_st"] is None
    assert row["b1_avg_st_n"] == 0
    assert row["b1_avg_st_official"] == pytest.approx(0.11)
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
