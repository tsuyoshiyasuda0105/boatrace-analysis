from datetime import date, datetime
from zoneinfo import ZoneInfo

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


def test_parse_jst_datetime_normalizes_naive_and_aware_values():
    naive = result_scraper._parse_jst_datetime("2026-08-08 16:30:00")
    aware = result_scraper._parse_jst_datetime("2026-08-08T07:30:00+00:00")

    assert naive is not None
    assert aware is not None
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    assert naive.utcoffset() == aware.utcoffset()
    assert naive.hour == 16
    assert aware.hour == 16


class _PendingRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _PendingRacesConn:
    def __init__(self, rows):
        self._rows = rows
        self._calls = 0

    def execute(self, sql, params=()):
        self._calls += 1
        assert params == ("2026-08-08",)
        if self._calls <= 2:
            return _PendingRowsResult(self._rows if self._calls == 1 else [])
        raise AssertionError(f"unexpected SQL call: {sql}")


def test_scrape_results_for_pending_races_accepts_mixed_timezone_values(monkeypatch):
    rows = [
        ("20260808-01-01", "2026-08-08 16:30:00"),
        ("20260808-01-02", datetime(2026, 8, 8, 7, 31, tzinfo=ZoneInfo("UTC"))),
    ]
    conn = _PendingRacesConn(rows)

    monkeypatch.setattr(
        result_scraper,
        "_now_jst",
        lambda: datetime(2026, 8, 8, 16, 40, tzinfo=ZoneInfo("Asia/Tokyo")),
    )
    monkeypatch.setattr(
        result_scraper,
        "scrape_race_result",
        lambda race_id: {
            "race_date": "2026-08-08",
            "race_stadium_number": 1,
            "race_number": int(race_id[-2:]),
            "race_kimarite": None,
            "boats": [],
            "payouts": {"trifecta": []},
            "weather": {},
        },
    )

    payload = result_scraper.scrape_results_for_pending_races(
        date(2026, 8, 8),
        conn,
        l4_only=False,
    )

    assert [row["race_number"] for row in payload["results"]] == [1, 2]
