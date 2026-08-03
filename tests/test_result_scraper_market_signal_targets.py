from datetime import date

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
