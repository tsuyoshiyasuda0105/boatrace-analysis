import sqlite3
from datetime import datetime


def test_original_exhibition_collected_at_uses_jst_clock(monkeypatch):
    from src.collectors import original_exhibition

    fake_now = datetime(2026, 8, 8, 7, 5, 0, tzinfo=original_exhibition.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == original_exhibition.JST
            return fake_now

    monkeypatch.setattr(original_exhibition, "datetime", _FakeDatetime)
    conn = sqlite3.connect(":memory:")
    original_exhibition.ensure_schema(conn)

    try:
        inserted = original_exhibition._upsert_rows(
            conn,
            "20260808-17-08",
            17,
            "2026-08-08",
            8,
            "miyajima",
            "https://example.com",
            [{"boat_number": 1, "lap_time": "6.71"}],
        )

        row = conn.execute(
            "SELECT collected_at FROM race_original_exhibitions WHERE race_id=?",
            ("20260808-17-08",),
        ).fetchone()

        assert inserted == 1
        assert row is not None
        assert row[0] == "2026-08-08T07:05:00"
    finally:
        conn.close()
