import sqlite3
from datetime import date, datetime, timedelta

from src.collectors import result_scraper


class _MarketSignalCacheConn:
    def execute(self, sql, params=()):
        assert "page_html_cache" in sql
        assert params == ("market_signals:last-good:2026-08-03",)
        return self

    def fetchone(self):
        return (
            '{"signals":{"20260803-17-09":{},"20260803-21-12":{}}}',
        )


def test_market_signal_candidate_ids_from_cache():
    got = result_scraper._market_signal_candidate_ids(
        _MarketSignalCacheConn(),
        date(2026, 8, 3),
    )

    assert got == {"20260803-17-09", "20260803-21-12"}


def _result_conn(race_count: int = 1):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
            race_id TEXT PRIMARY KEY,
            race_date TEXT,
            stadium_number INTEGER,
            race_closed_at TEXT
        );
        CREATE TABLE race_entries (
            race_id TEXT,
            boat_number INTEGER,
            class_number INTEGER
        );
        CREATE TABLE predictions (
            race_id TEXT,
            boat_number INTEGER,
            prob_first REAL
        );
        CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT);
        CREATE TABLE race_results (race_id TEXT, kimarite TEXT);
        """
    )
    now = datetime(2026, 8, 12, 10, 0)
    for race_no in range(1, race_count + 1):
        race_id = f"20260812-21-{race_no:02d}"
        conn.execute(
            "INSERT INTO races VALUES (?, ?, ?, ?)",
            (race_id, "2026-08-12", 21, (now - timedelta(minutes=61 + race_no)).isoformat()),
        )
        conn.execute("INSERT INTO race_entries VALUES (?, 1, 2)", (race_id,))
        conn.execute("INSERT INTO predictions VALUES (?, 1, 0.5)", (race_id,))
    return conn, now


def _result_payload(race_id: str):
    date_str, stadium, race_no = race_id.split("-")
    return {
        "race_date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        "race_stadium_number": int(stadium),
        "race_number": int(race_no),
        "boats": [],
        "payouts": {"trifecta": []},
    }


def test_non_candidate_result_is_repaired_after_delay(monkeypatch):
    conn, now = _result_conn()
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "scrape_race_result", _result_payload)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())

    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    assert got["target_count"] == 1
    assert len(got["results"]) == 1


def test_non_candidate_result_waits_for_openapi(monkeypatch):
    conn, now = _result_conn()
    conn.execute(
        "UPDATE races SET race_closed_at = ?",
        ((now - timedelta(minutes=59)).isoformat(),),
    )
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "scrape_race_result", _result_payload)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())

    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    assert got["target_count"] == 0


def test_result_repair_batch_is_capped(monkeypatch):
    conn, now = _result_conn(race_count=13)
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "scrape_race_result", _result_payload)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())

    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    assert got["target_count"] == 12
    assert len(got["results"]) == 12


def test_market_signal_result_is_prioritized_before_repair_backlog(monkeypatch):
    conn, now = _result_conn(race_count=13)
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "scrape_race_result", _result_payload)
    monkeypatch.setattr(
        result_scraper,
        "_market_signal_candidate_ids",
        lambda *_: {"20260812-21-13"},
    )

    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    assert got["target_count"] == 12
    assert any(race["race_number"] == 13 for race in got["results"])


def test_result_repair_rotates_past_persistently_failing_old_races(monkeypatch):
    conn, now = _result_conn(race_count=30)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())
    monkeypatch.setattr(result_scraper, "scrape_race_result", _result_payload)

    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    first = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now + timedelta(minutes=5))
    second = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    first_races = {row["race_number"] for row in first["results"]}
    second_races = {row["race_number"] for row in second["results"]}
    assert len(first_races) == 12
    assert len(second_races) == 12
    assert first_races != second_races
