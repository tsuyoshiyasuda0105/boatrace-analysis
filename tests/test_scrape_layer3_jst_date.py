import importlib.util
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_scrape_layer3():
    spec = importlib.util.spec_from_file_location(
        "scrape_layer3",
        ROOT / "scripts" / "scrape_layer3.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_today_jst_uses_tokyo_calendar(monkeypatch):
    mod = _load_scrape_layer3()
    fake_now = datetime(2026, 8, 8, 0, 30, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst() == date(2026, 8, 8)
