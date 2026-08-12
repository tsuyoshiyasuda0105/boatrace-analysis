import sqlite3

from scripts.cache_racer_accident_rank_snapshot import RULE_VERSION, build_snapshot
from scripts.check_external_accident_snapshot import load_internal_rows


def _init_snapshot_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE racer_accident_period_stats (
          racer_number INTEGER,
          period_year INTEGER,
          period_half INTEGER,
          period_start TEXT,
          period_end TEXT,
          starts_count INTEGER,
          accident_events INTEGER,
          accident_points INTEGER,
          accident_rate REAL,
          rule_version TEXT,
          source_kind TEXT,
          updated_at TEXT
        );
        CREATE TABLE racers (
          racer_number INTEGER PRIMARY KEY,
          name TEXT
        );
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT
        );
        CREATE TABLE race_entries (
          race_id TEXT,
          boat_number INTEGER,
          racer_number INTEGER,
          racer_name TEXT,
          class_number INTEGER
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO racer_accident_period_stats
          (racer_number, period_year, period_half, period_start, period_end,
           starts_count, accident_events, accident_points, accident_rate,
           rule_version, source_kind, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            (5001, 2027, 1, "2026-05-01", "2026-08-05", 10, 1, 20, 2.0, RULE_VERSION, "reconstructed"),
            (5001, 2027, 1, "2026-05-01", "2026-08-05", 10, 1, 40, 4.0, RULE_VERSION, "official_external"),
        ],
    )
    conn.execute("INSERT INTO racers (racer_number, name) VALUES (5001, '外部優先選手')")
    conn.commit()
    conn.close()


def test_build_snapshot_prefers_official_external(tmp_path):
    db_path = tmp_path / "accident.sqlite3"
    _init_snapshot_db(str(db_path))

    summary = build_snapshot("2026-08-05", "2026-05-01", str(db_path))

    assert summary["source_kind"] == "official_external"

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT source_kind, accident_points, accident_rate
          FROM racer_accident_rank_snapshots
         WHERE period_start = '2026-05-01' AND racer_number = 5001
        """
    ).fetchone()
    conn.close()

    assert row == ("official_external", 40, 4.0)


def test_build_snapshot_prefers_fresh_internal_rebuild_over_stale_external(tmp_path):
    db_path = tmp_path / "fresh-internal.sqlite3"
    _init_snapshot_db(str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO racer_accident_period_stats
          (racer_number, period_year, period_half, period_start, period_end,
           starts_count, accident_events, accident_points, accident_rate,
           rule_version, source_kind, updated_at)
        VALUES (5001, 2027, 1, '2026-05-01', '2026-08-12',
                12, 2, 30, 2.5, ?, 'internal_rebuild', CURRENT_TIMESTAMP)
        """,
        (RULE_VERSION,),
    )
    conn.commit()
    conn.close()

    summary = build_snapshot("2026-08-12", "2026-05-01", str(db_path))

    assert summary["source_kind"] == "internal_rebuild"
    assert summary["period_end"] == "2026-08-12"


def test_load_internal_rows_reads_reconstructed_source(tmp_path):
    db_path = tmp_path / "compare.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE racer_accident_period_stats (
          racer_number INTEGER,
          starts_count INTEGER,
          accident_points INTEGER,
          accident_rate REAL,
          period_end TEXT,
          period_start TEXT,
          source_kind TEXT,
          rule_version TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO racer_accident_period_stats
          (racer_number, starts_count, accident_points, accident_rate, period_end, period_start, source_kind, rule_version)
        VALUES (?, ?, ?, ?, ?, '2026-05-01', 'reconstructed', ?)
        """,
        [
            (5002, 40, 5, 0.12, "2026-08-05", RULE_VERSION),
            (5002, 55, 10, 0.18, "2026-08-11", RULE_VERSION),
        ],
    )

    rows = load_internal_rows(conn, "2026-05-01")
    conn.close()

    assert rows[5002]["starts_count"] == 55
    assert rows[5002]["accident_points"] == 10
    assert rows[5002]["accident_rate"] == 0.18
    assert rows[5002]["period_end"] == "2026-08-11"
