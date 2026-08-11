from datetime import datetime, timezone

from scripts import poll_results
from src.collectors import result_scraper


def test_aware_utc_close_time_is_normalized_to_jst_naive():
    closed_at = datetime(2026, 8, 11, 0, 32, tzinfo=timezone.utc)

    assert result_scraper._coerce_jst_naive(closed_at) == datetime(2026, 8, 11, 9, 32)
    assert poll_results._parse_closed_at(closed_at) == datetime(2026, 8, 11, 9, 32)


def test_naive_database_close_time_stays_jst_naive():
    assert result_scraper._coerce_jst_naive("2026-08-11 09:32:00") == datetime(
        2026, 8, 11, 9, 32
    )
