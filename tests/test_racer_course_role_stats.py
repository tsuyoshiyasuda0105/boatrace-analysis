from pathlib import Path

import pytest

from scripts import build_racer_course_role_stats as mod
from src.db.connection import connect as db_connect


SNAPSHOT_DATE = "2026-09-01"
RACER = 1001


@pytest.fixture
def conn(tmp_path: Path):
    connection = db_connect(str(tmp_path / "course-role.db"))
    connection.executescript(
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
        CREATE TABLE race_results (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          finishing_position INTEGER,
          course_number INTEGER
        );
        """
    )
    connection.execute("INSERT INTO races VALUES ('target', ?)", (SNAPSHOT_DATE,))
    connection.execute("INSERT INTO race_entries VALUES ('target', 1, ?)", (RACER,))
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def _add_race(
    conn,
    race_id: str,
    race_date: str,
    course: int | None,
    *,
    target_finish: int | None,
    course1_wins: bool,
    target_boat: int | None = None,
) -> None:
    target_boat = course if target_boat is None else target_boat
    assert target_boat is not None
    other_boat = 2 if target_boat == 1 else 1
    conn.execute("INSERT INTO races VALUES (?, ?)", (race_id, race_date))
    entries = [(race_id, target_boat, RACER), (race_id, other_boat, 9000 + other_boat)]
    other_finish = 1 if course1_wins and other_boat == 1 else 3
    results = [
        (race_id, target_boat, target_finish, course),
        (race_id, other_boat, other_finish, other_boat),
    ]
    if not course1_wins and target_finish != 1:
        entries.append((race_id, 3, 9003))
        results.append((race_id, 3, 1, 3))
    conn.executemany("INSERT INTO race_entries VALUES (?, ?, ?)", entries)
    conn.executemany("INSERT INTO race_results VALUES (?, ?, ?, ?)", results)
    conn.commit()


def _row(conn) -> tuple:
    rows = mod.build_rows(conn, SNAPSHOT_DATE)
    return next(row for row in rows if row[1] == RACER)


def test_course1_escape_rate_is_correct(conn) -> None:
    for index, won in enumerate((True, True, True, False), start=1):
        _add_race(
            conn,
            f"c1-{index}",
            f"2026-08-{index:02d}",
            1,
            target_finish=1 if won else 2,
            course1_wins=won,
        )

    row = _row(conn)
    assert row[3:6] == (4, 3, 0.75)


def test_course2_nigashi_rate_is_correct(conn) -> None:
    for index, nigashi in enumerate((True, True, True, False), start=1):
        _add_race(
            conn,
            f"c2-{index}",
            f"2026-07-{index:02d}",
            2,
            target_finish=2,
            course1_wins=nigashi,
        )

    row = _row(conn)
    assert row[6:9] == (4, 3, 0.75)


def test_race_older_than_window_is_excluded(conn) -> None:
    _add_race(conn, "old", "2025-08-31", 1, target_finish=1, course1_wins=True)

    row = _row(conn)
    assert row[3] == 0


def test_snapshot_date_race_is_excluded(conn) -> None:
    _add_race(conn, "same-day", SNAPSHOT_DATE, 1, target_finish=1, course1_wins=True)

    row = _row(conn)
    assert row[3] == 0


def test_rate_is_null_when_course_has_no_starts(conn) -> None:
    _add_race(conn, "only-c1", "2026-08-01", 1, target_finish=1, course1_wins=True)

    row = _row(conn)
    assert row[6] == 0
    assert row[8] is None


def test_duplicate_winners_do_not_duplicate_nigashi_count(conn) -> None:
    _add_race(conn, "duplicate-win", "2026-08-01", 2, target_finish=2, course1_wins=True)
    conn.execute("INSERT INTO race_entries VALUES ('duplicate-win', 3, 9003)")
    conn.execute("INSERT INTO race_results VALUES ('duplicate-win', 3, 1, 3)")
    conn.commit()

    row = _row(conn)
    assert row[6] == 1
    assert row[7] == 1


def test_null_finish_is_excluded_from_starts(conn) -> None:
    _add_race(conn, "disqualified", "2026-08-01", 2, target_finish=None, course1_wins=True)

    row = _row(conn)
    assert row[6] == 0
    assert row[8] is None


def test_null_course_number_falls_back_to_boat_number(conn) -> None:
    _add_race(
        conn,
        "null-course",
        "2026-08-01",
        None,
        target_boat=2,
        target_finish=2,
        course1_wins=True,
    )

    row = _row(conn)
    assert row[6:9] == (1, 1, 1.0)


def test_zero_course_number_falls_back_to_boat_number(conn) -> None:
    _add_race(
        conn,
        "zero-course",
        "2026-08-01",
        0,
        target_boat=2,
        target_finish=2,
        course1_wins=True,
    )

    row = _row(conn)
    assert row[6:9] == (1, 1, 1.0)


def test_rerun_upserts_without_duplicates_and_updates_values(conn) -> None:
    _add_race(conn, "first", "2026-08-01", 1, target_finish=2, course1_wins=False)
    mod.upsert_rows(conn, mod.build_rows(conn, SNAPSHOT_DATE))

    conn.execute(
        "UPDATE race_results SET finishing_position = 1 WHERE race_id = 'first' AND boat_number = 1"
    )
    conn.commit()
    mod.upsert_rows(conn, mod.build_rows(conn, SNAPSHOT_DATE))

    count = conn.execute(
        "SELECT COUNT(*) FROM racer_course_role_snapshots WHERE snapshot_date = ?",
        (SNAPSHOT_DATE,),
    ).fetchone()[0]
    rate = conn.execute(
        "SELECT course1_win_rate FROM racer_course_role_snapshots WHERE snapshot_date = ?",
        (SNAPSHOT_DATE,),
    ).fetchone()[0]
    assert count == 1
    assert rate == 1.0


def test_main_builds_snapshot_with_local_sqlite(conn, monkeypatch) -> None:
    connect_calls = []

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return conn

    monkeypatch.setattr(mod, "db_connect", fake_connect)
    monkeypatch.setattr(
        mod.sys,
        "argv",
        ["build_racer_course_role_stats.py", "--local", "--date", SNAPSHOT_DATE],
    )
    _add_race(conn, "main", "2026-08-01", 1, target_finish=1, course1_wins=True)

    assert mod.main() == 0
    assert connect_calls == [((), {"direct": True})]
    assert conn.execute("SELECT COUNT(*) FROM racer_course_role_snapshots").fetchone()[0] == 1
    indexes = conn.execute("PRAGMA index_list(racer_course_role_snapshots)").fetchall()
    assert all(index[1] != "idx_racer_course_role_snapshot_date" for index in indexes)


def test_main_returns_one_for_invalid_date(conn, monkeypatch) -> None:
    def unexpected_connect(*args, **kwargs):
        pytest.fail("invalid --date must be rejected before opening the database")

    monkeypatch.setattr(mod, "db_connect", unexpected_connect)
    monkeypatch.setattr(
        mod.sys,
        "argv",
        ["build_racer_course_role_stats.py", "--local", "--date", "not-a-date"],
    )

    assert mod.main() == 1
