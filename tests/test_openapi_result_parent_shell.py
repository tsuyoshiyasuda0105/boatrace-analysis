import sqlite3

from src.collectors import openapi


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT NOT NULL,
          stadium_number INTEGER NOT NULL,
          race_number INTEGER NOT NULL,
          race_grade_number INTEGER,
          race_title TEXT,
          race_subtitle TEXT,
          race_distance INTEGER,
          race_closed_at TEXT
        );
        CREATE TABLE race_previews (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          weather_number INTEGER,
          wind_speed REAL,
          wind_direction_number INTEGER,
          wave_height REAL,
          temperature REAL,
          water_temperature REAL,
          course_number INTEGER,
          exhibition_time REAL,
          start_timing_exhibition REAL,
          weight_adjustment REAL,
          tilt_adjustment REAL,
          PRIMARY KEY (race_id, boat_number),
          FOREIGN KEY (race_id) REFERENCES races(race_id)
        );
        CREATE TABLE race_results (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          finishing_position INTEGER,
          course_number INTEGER,
          start_timing REAL,
          race_time TEXT,
          remarks TEXT,
          kimarite TEXT,
          PRIMARY KEY (race_id, boat_number),
          FOREIGN KEY (race_id) REFERENCES races(race_id)
        );
        CREATE TABLE race_payouts (
          race_id TEXT NOT NULL,
          bet_type TEXT NOT NULL,
          combination TEXT NOT NULL,
          payout INTEGER NOT NULL,
          popularity INTEGER,
          PRIMARY KEY (race_id, bet_type, combination),
          FOREIGN KEY (race_id) REFERENCES races(race_id)
        );
        """
    )
    return conn


def test_upsert_results_creates_parent_race_shell_before_child_rows():
    conn = _conn()
    payload = {
        "results": [
            {
                "race_date": "2026-08-10",
                "race_stadium_number": 21,
                "race_number": 1,
                "race_closed_at": "2026-08-10 19:58:00",
                "race_kimarite": "逃げ",
                "boats": [
                    {
                        "racer_boat_number": 1,
                        "racer_place_number": 1,
                        "racer_course_number": 1,
                        "racer_start_timing": 0.12,
                        "racer_race_time": "1.49.8",
                        "racer_remarks": None,
                    },
                    {
                        "racer_boat_number": 2,
                        "racer_place_number": 2,
                        "racer_course_number": 2,
                        "racer_start_timing": 0.16,
                        "racer_race_time": "1.50.9",
                        "racer_remarks": None,
                    },
                ],
                "payouts": {
                    "trifecta": [
                        {"combination": "1-2-3", "payout": 1450, "popularity": 4}
                    ]
                },
            }
        ]
    }

    inserted = openapi.upsert_results(conn, payload)
    conn.commit()

    assert inserted == 2
    race = conn.execute(
        "SELECT race_date, stadium_number, race_number, race_closed_at FROM races WHERE race_id = ?",
        ("20260810-21-01",),
    ).fetchone()
    assert race == ("2026-08-10", 21, 1, "2026-08-10 19:58:00")
    winner = conn.execute(
        "SELECT finishing_position, kimarite FROM race_results WHERE race_id = ? AND boat_number = 1",
        ("20260810-21-01",),
    ).fetchone()
    assert winner == (1, "逃げ")
    payout = conn.execute(
        "SELECT payout FROM race_payouts WHERE race_id = ? AND bet_type = ? AND combination = ?",
        ("20260810-21-01", "trifecta", "1-2-3"),
    ).fetchone()
    assert payout == (1450,)


def test_upsert_previews_creates_parent_race_shell_before_preview_rows():
    conn = _conn()
    payload = {
        "previews": [
            {
                "race_date": "2026-08-10",
                "race_stadium_number": 21,
                "race_number": 1,
                "race_closed_at": "2026-08-10 19:58:00",
                "race_weather_number": 1,
                "race_wind": 3,
                "race_wind_direction_number": 2,
                "race_wave": 1,
                "race_temperature": 29,
                "race_water_temperature": 31,
                "boats": {
                    "1": {
                        "racer_boat_number": 1,
                        "racer_course_number": 1,
                        "racer_exhibition_time": 6.79,
                        "racer_start_timing": 0.11,
                        "racer_weight_adjustment": 0.0,
                        "racer_tilt_adjustment": -0.5,
                    }
                },
            }
        ]
    }

    inserted = openapi.upsert_previews(conn, payload)
    conn.commit()

    assert inserted == 1
    race = conn.execute(
        "SELECT race_date, stadium_number, race_number FROM races WHERE race_id = ?",
        ("20260810-21-01",),
    ).fetchone()
    assert race == ("2026-08-10", 21, 1)
    preview = conn.execute(
        "SELECT exhibition_time, start_timing_exhibition FROM race_previews WHERE race_id = ? AND boat_number = 1",
        ("20260810-21-01",),
    ).fetchone()
    assert preview == (6.79, 0.11)


def test_upsert_results_skips_only_broken_race_and_keeps_other_races():
    conn = _conn()
    payload = {
        "results": [
            {
                "race_date": "2026-08-10",
                "race_stadium_number": 21,
                "race_number": 1,
                "race_closed_at": "2026-08-10 19:58:00",
                "boats": [
                    {
                        "racer_boat_number": 1,
                        "racer_place_number": 1,
                        "racer_course_number": 1,
                        "racer_start_timing": 0.12,
                        "racer_race_time": "1.49.8",
                    }
                ],
            },
            {
                "race_date": "2026-08-10",
                "race_stadium_number": 21,
                "race_number": 2,
                "race_closed_at": "2026-08-10 20:20:00",
                "boats": [
                    {
                        "racer_boat_number": None,
                        "racer_place_number": 1,
                        "racer_course_number": 1,
                        "racer_start_timing": 0.11,
                        "racer_race_time": "1.49.1",
                    }
                ],
            },
        ]
    }

    inserted = openapi.upsert_results(conn, payload)
    conn.commit()

    assert inserted == 1
    kept = conn.execute(
        "SELECT COUNT(*) FROM race_results WHERE race_id = ?",
        ("20260810-21-01",),
    ).fetchone()
    assert kept == (1,)
    skipped = conn.execute(
        "SELECT COUNT(*) FROM race_results WHERE race_id = ?",
        ("20260810-21-02",),
    ).fetchone()
    assert skipped == (0,)
