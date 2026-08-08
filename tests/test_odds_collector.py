from __future__ import annotations

from datetime import date
from pathlib import Path

from src.collectors._http import FetchHtmlResult
from src.collectors import odds


def _create_test_db(tmp_path: Path) -> str:
    db_path = tmp_path / "odds_test.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT NOT NULL,
          stadium_number INTEGER NOT NULL,
          race_number INTEGER NOT NULL
        );
        CREATE TABLE race_results (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE odds_trifecta (
          race_id TEXT NOT NULL,
          combination TEXT NOT NULL,
          odds REAL NOT NULL,
          is_final INTEGER NOT NULL,
          recorded_at TEXT NOT NULL,
          snapshot_label TEXT,
          PRIMARY KEY (race_id, combination, recorded_at)
        );
        """
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _insert_race(db_path: str, race_id: str, race_date: str = "2026-08-08") -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO races(race_id, race_date, stadium_number, race_number) VALUES (?, ?, ?, ?)",
        (race_id, race_date, 1, 1),
    )
    conn.commit()
    conn.close()


def _full_odds_map() -> dict[str, float]:
    odds_map: dict[str, float] = {}
    value = 1.0
    for a in range(1, 7):
        for b in range(1, 7):
            for c in range(1, 7):
                if len({a, b, c}) != 3:
                    continue
                odds_map[f"{a}-{b}-{c}"] = value
                value += 1.0
    assert len(odds_map) == 120
    return odds_map


def test_list_target_races_includes_partial_race(tmp_path: Path) -> None:
    db_path = _create_test_db(tmp_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO races VALUES ('20260808-01-01', '2026-08-08', 1, 1)")
    conn.execute("INSERT INTO races VALUES ('20260808-01-02', '2026-08-08', 1, 2)")
    conn.execute(
        "INSERT INTO odds_trifecta VALUES (?, ?, ?, ?, ?, ?)",
        ("20260808-01-01", "1-2-3", 12.3, 0, "2026-08-08T10:00:00+00:00", "T-5min"),
    )
    full_map = _full_odds_map()
    conn.executemany(
        "INSERT INTO odds_trifecta VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("20260808-01-02", combo, odd, 0, "2026-08-08T10:00:00+00:00", "T-5min")
            for combo, odd in full_map.items()
        ],
    )
    conn.commit()

    targets = odds._list_target_races(conn, date(2026, 8, 8), force=False)
    conn.close()

    assert targets == [("20260808-01-01", 1, 1)]


def test_collect_one_race_records_retry_waiting_on_timeout(tmp_path: Path, monkeypatch) -> None:
    db_path = _create_test_db(tmp_path)
    race_id = "20260808-01-01"
    _insert_race(db_path, race_id)

    monkeypatch.setattr(
        odds,
        "fetch_html_detailed",
        lambda url: FetchHtmlResult(
            ok=False,
            html=None,
            error_type="timeout",
            retryable=True,
            attempts=3,
        ),
    )

    result = odds.collect_one_race(race_id, snapshot_label="T-5min", db_path=db_path)

    assert result["error"] == "timeout"
    assert result["fetch_state"] == "retry_waiting"

    import sqlite3

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT state, detail_code, retryable, attempts FROM odds_fetch_status WHERE race_id = ? AND snapshot_label = ?",
        (race_id, "T-5min"),
    ).fetchone()
    conn.close()
    assert row == ("retry_waiting", "timeout", 1, 3)


def test_collect_one_race_records_partial_data(tmp_path: Path, monkeypatch) -> None:
    db_path = _create_test_db(tmp_path)
    race_id = "20260808-01-01"
    _insert_race(db_path, race_id)

    monkeypatch.setattr(
        odds,
        "fetch_html_detailed",
        lambda url: FetchHtmlResult(ok=True, html="<html></html>", attempts=1),
    )
    monkeypatch.setattr(odds, "parse_trifecta_odds", lambda html: {"1-2-3": 9.9})

    result = odds.collect_one_race(race_id, snapshot_label="T-5min", db_path=db_path)

    assert result["error"] == "partial_data"
    assert result["fetch_state"] == "missing"
    assert result["missing_combinations"] == 119
    assert result["odds_inserted"] == 1

    import sqlite3

    conn = sqlite3.connect(db_path)
    status_row = conn.execute(
        "SELECT state, detail_code, combination_count FROM odds_fetch_status WHERE race_id = ? AND snapshot_label = ?",
        (race_id, "T-5min"),
    ).fetchone()
    odds_count = conn.execute(
        "SELECT COUNT(*) FROM odds_trifecta WHERE race_id = ?",
        (race_id,),
    ).fetchone()[0]
    conn.close()
    assert status_row == ("missing", "partial_data", 1)
    assert odds_count == 1


def test_collect_one_race_records_fetched_on_complete_data(tmp_path: Path, monkeypatch) -> None:
    db_path = _create_test_db(tmp_path)
    race_id = "20260808-01-01"
    _insert_race(db_path, race_id)

    monkeypatch.setattr(
        odds,
        "fetch_html_detailed",
        lambda url: FetchHtmlResult(ok=True, html="<html></html>", attempts=1),
    )
    monkeypatch.setattr(odds, "parse_trifecta_odds", lambda html: _full_odds_map())

    result = odds.collect_one_race(race_id, snapshot_label="T-5min", db_path=db_path)

    assert result["fetch_state"] == "fetched"
    assert result["odds_inserted"] == 120
    assert "error" not in result

    import sqlite3

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT state, detail_code, combination_count, last_success_at IS NOT NULL FROM odds_fetch_status WHERE race_id = ? AND snapshot_label = ?",
        (race_id, "T-5min"),
    ).fetchone()
    conn.close()
    assert row == ("fetched", "fetched", 120, 1)
