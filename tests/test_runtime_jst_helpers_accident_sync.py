import importlib.util
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sync_l4_summary_today_uses_jst(monkeypatch):
    mod = _load("sync_l4_summary_to_supabase", "scripts/sync_l4_summary_to_supabase.py")
    fake_now = datetime(2026, 8, 8, 0, 50, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._now_jst() == fake_now


def test_cache_course1_stats_today_uses_jst(monkeypatch):
    mod = _load("cache_course1_stats", "scripts/cache_course1_stats.py")
    fake_now = datetime(2026, 8, 8, 0, 52, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_cache_racer_accident_rank_snapshot_today_uses_jst(monkeypatch):
    mod = _load("cache_racer_accident_rank_snapshot", "scripts/cache_racer_accident_rank_snapshot.py")
    fake_now = datetime(2026, 8, 8, 0, 54, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"


def test_check_external_accident_snapshot_today_uses_jst(monkeypatch):
    mod = _load("check_external_accident_snapshot", "scripts/check_external_accident_snapshot.py")
    fake_now = datetime(2026, 8, 8, 0, 56, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"


def test_backfill_accident_dent_daily_cache_today_uses_jst(monkeypatch):
    mod = _load("backfill_accident_dent_daily_cache", "scripts/backfill_accident_dent_daily_cache.py")
    fake_now = datetime(2026, 8, 8, 0, 58, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"
