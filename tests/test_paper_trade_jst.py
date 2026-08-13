import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_paper_trade():
    spec = importlib.util.spec_from_file_location(
        "paper_trade",
        ROOT / "scripts" / "paper_trade.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_paper_trade_today_uses_jst_calendar(monkeypatch):
    mod = _load_paper_trade()
    fake_now = datetime(2026, 8, 8, 0, 10, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"


def test_paper_trade_now_iso_omits_offset_for_existing_storage(monkeypatch):
    mod = _load_paper_trade()
    fake_now = datetime(2026, 8, 8, 6, 45, 30, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._now_jst_iso() == "2026-08-08T06:45:30"
