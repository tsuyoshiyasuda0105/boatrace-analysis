from pathlib import Path

import pytest

from src.access_stats import (
    access_coverage,
    ensure_site_access_table,
    load_daily_access,
    record_daily_access,
)
from src.db.connection import connect as db_connect


@pytest.fixture
def conn(tmp_path: Path):
    connection = db_connect(str(tmp_path / "access.db"))
    ensure_site_access_table(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_records_reported_days_and_zero_fills_the_rest(conn):
    """Cloudflare は表示ゼロの日を返さないので、窓の中の欠落日は 0 として埋める。"""
    result = record_daily_access(
        conn,
        [{"date": "2026-09-03", "page_views": 10, "visits": 2}],
        window_start="2026-09-01",
        window_end="2026-09-05",
    )

    assert result == {"written": 5, "from_source": 1, "zero_filled": 4}
    rows = load_daily_access(conn, start="2026-09-01", end="2026-09-05")
    assert [r["views"] for r in rows] == [0, 0, 10, 0, 0]


def test_does_not_zero_out_days_outside_the_fetch_window(conn):
    """保持期間を過ぎて取得できなくなった日を、後の取り込みが 0 で潰さないこと。"""
    record_daily_access(
        conn,
        [{"date": "2026-08-01", "page_views": 500, "visits": 40}],
        window_start="2026-08-01",
        window_end="2026-08-01",
    )

    # 30日経ち、Cloudflare が 8/01 を返さなくなった後の取り込み。
    record_daily_access(
        conn,
        [{"date": "2026-09-05", "page_views": 7, "visits": 1}],
        window_start="2026-09-01",
        window_end="2026-09-05",
    )

    kept = load_daily_access(conn, start="2026-08-01", end="2026-08-01")
    assert kept[0]["views"] == 500, "保持期間外の実績が 0 で上書きされてはいけない"


def test_upsert_overwrites_the_same_day(conn):
    """数値は後から確定することがあるので、同じ日は最新値で上書きする。"""
    window = {"window_start": "2026-09-05", "window_end": "2026-09-05"}
    record_daily_access(conn, [{"date": "2026-09-05", "page_views": 3, "visits": 1}], **window)
    record_daily_access(conn, [{"date": "2026-09-05", "page_views": 9, "visits": 4}], **window)

    rows = load_daily_access(conn, start="2026-09-05", end="2026-09-05")
    assert rows[0]["views"] == 9
    assert rows[0]["visits"] == 4
    assert access_coverage(conn)["days"] == 1, "重複行が増えていないこと"


def test_load_returns_a_continuous_range(conn):
    """グラフの日付が飛ばないよう、記録の無い日も 0 で連続させて返す。"""
    record_daily_access(
        conn,
        [{"date": "2026-09-02", "page_views": 4, "visits": 1}],
        window_start="2026-09-02",
        window_end="2026-09-02",
    )

    rows = load_daily_access(conn, start="2026-09-01", end="2026-09-04")
    assert [r["date"] for r in rows] == [
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
    ]
    assert [r["recorded"] for r in rows] == [False, True, False, False]


def test_coverage_on_empty_ledger(conn):
    assert access_coverage(conn) == {
        "first_date": None, "last_date": None, "days": 0, "total_views": 0,
    }
