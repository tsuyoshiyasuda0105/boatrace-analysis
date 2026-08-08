import sqlite3

from scripts import build_racer_entry_change_stats as mod


def test_classify_level_thresholds() -> None:
    assert mod.classify_level(99, 0.50) is None
    assert mod.classify_level(100, 0.14) is None
    assert mod.classify_level(100, 0.15) == "watch"
    assert mod.classify_level(100, 0.20) == "high"


def test_build_rows_counts_only_target_day_entrants_and_computes_inner_outer() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT NOT NULL
        );
        CREATE TABLE race_entries (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          racer_number INTEGER,
          PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE race_previews (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          course_number INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO races(race_id, race_date) VALUES (?, ?)",
        [
            ("r1", "2026-08-07"),
            ("r2", "2026-08-07"),
            ("r3", "2026-08-08"),
        ],
    )
    conn.executemany(
        "INSERT INTO race_entries(race_id, boat_number, racer_number) VALUES (?, ?, ?)",
        [
            ("r1", 2, 1001),
            ("r1", 4, 1002),
            ("r2", 3, 1001),
            ("r2", 5, 1002),
            ("r3", 1, 1001),
            ("r3", 2, 1003),
        ],
    )
    conn.executemany(
        "INSERT INTO race_previews(race_id, boat_number, course_number) VALUES (?, ?, ?)",
        [
            ("r1", 2, 1),  # 1001 inner
            ("r1", 4, 5),  # 1002 outer
            ("r2", 3, 3),  # 1001 stay
            ("r2", 5, 4),  # 1002 inner
            ("r3", 1, 1),  # same-day row must be ignored
        ],
    )

    rows = mod.build_rows(conn, "2026-08-08")
    by_racer = {row[1]: row for row in rows}

    assert set(by_racer) == {1001, 1003}

    racer_1001 = by_racer[1001]
    assert racer_1001[2] == 2
    assert racer_1001[3] == 1
    assert racer_1001[4] == 0.5
    assert racer_1001[5] == 1
    assert racer_1001[6] == 0.5
    assert racer_1001[7] == 0
    assert racer_1001[8] == 0.0

    racer_1003 = by_racer[1003]
    assert racer_1003[2] == 0
    assert racer_1003[3] == 0
    assert racer_1003[4] == 0.0
    assert racer_1003[5] == 0
    assert racer_1003[6] == 0.0
    assert racer_1003[7] == 0
    assert racer_1003[8] == 0.0
