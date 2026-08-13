import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_daily_collect_today_uses_jst(monkeypatch):
    mod = _load("daily_collect", "scripts/daily_collect.py")
    fake_now = datetime(2026, 8, 8, 0, 32, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_check_data_quality_today_uses_jst(monkeypatch):
    mod = _load("check_data_quality", "scripts/check_data_quality.py")
    fake_now = datetime(2026, 8, 8, 0, 36, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"


def test_check_strategy_health_today_uses_jst(monkeypatch):
    mod = _load("check_strategy_health", "scripts/check_strategy_health.py")
    fake_now = datetime(2026, 8, 8, 0, 40, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_sync_to_supabase_today_uses_jst(monkeypatch):
    mod = _load("sync_to_supabase", "scripts/sync_to_supabase.py")
    fake_now = datetime(2026, 8, 8, 0, 44, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"
