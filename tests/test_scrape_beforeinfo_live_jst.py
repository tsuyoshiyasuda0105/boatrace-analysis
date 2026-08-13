from datetime import datetime

from scripts import scrape_beforeinfo_live as live


def test_scrape_beforeinfo_live_today_and_now_iso_use_jst(monkeypatch):
    fake_now = datetime(2026, 8, 8, 0, 30, tzinfo=live.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == live.JST
            return fake_now

    monkeypatch.setattr(live, "datetime", _FakeDatetime)

    assert live._today_jst_iso() == "2026-08-08"
    assert live._now_jst_iso() == "2026-08-08T00:30:00"
