from __future__ import annotations

from pathlib import Path
import sqlite3

from scripts.build_asof_features import parser
from src.features.odds_sync import sync_odds


def _source() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE odds_trifecta ("
        "race_id TEXT, combination TEXT, odds REAL, is_final INTEGER, "
        "recorded_at TEXT, snapshot_label TEXT)"
    )
    connection.executemany(
        "INSERT INTO odds_trifecta VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("20260502-01-01", "1-2-3", 12.0, 0, "2026-05-02T10:00:00", "T-5min"),
            ("20260502-01-01", "1-2-3", 11.0, 0, "2026-05-02T10:01:00", "T-5min"),
            ("20260502-01-01", "1-2-3", 10.0, 1, "2026-05-02T10:05:00", "final"),
            ("20260502-01-01", "1-2-3", 9.0, 0, "2026-05-02T10:03:00", "T-1min"),
            ("20260503-01-01", "1-2-3", 20.0, 1, "2026-05-03T10:05:00", "final"),
        ],
    )
    return connection


def test_sync_odds_is_append_only_and_keeps_latest_supported_snapshots(tmp_path: Path) -> None:
    destination = tmp_path / "search.db"
    source = _source()

    first = sync_odds(source, destination, "2026-05-02", "2026-05-02")
    second = sync_odds(source, destination, "2026-05-02", "2026-05-02")

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["skipped"] == 2
    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT snapshot, odds FROM odds_snapshot ORDER BY snapshot"
        ).fetchall()
    assert rows == [("T-5min", 11.0), ("final", 10.0)]


def test_sync_odds_rebuild_replaces_only_the_requested_period(tmp_path: Path) -> None:
    destination = tmp_path / "search.db"
    source = _source()
    sync_odds(source, destination, "2026-05-02", "2026-05-03")
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "UPDATE odds_snapshot SET odds = 99 WHERE race_id = '20260502-01-01' AND snapshot = 'final'"
        )

    result = sync_odds(
        source, destination, "2026-05-02", "2026-05-02", rebuild=True
    )

    assert result["removed"] == 2
    with sqlite3.connect(destination) as connection:
        values = dict(
            connection.execute(
                "SELECT race_id, odds FROM odds_snapshot WHERE snapshot = 'final'"
            ).fetchall()
        )
    assert values == {"20260502-01-01": 10.0, "20260503-01-01": 20.0}


def test_build_cli_accepts_sync_odds_mode() -> None:
    args = parser().parse_args(
        ["--sync-odds", "--date-from", "2026-05-02", "--date-to", "2026-05-03"]
    )
    assert args.sync_odds is True
