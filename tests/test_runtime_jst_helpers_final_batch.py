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


def test_backfill_official_today_uses_jst(monkeypatch):
    mod = _load("backfill_official", "scripts/backfill_official.py")
    fake_now = datetime(2026, 8, 8, 1, 0, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_build_derived_start_stats_uses_jst(monkeypatch):
    mod = _load("build_derived_start_stats", "scripts/build_derived_start_stats.py")
    fake_now = datetime(2026, 8, 8, 1, 2, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"
    assert mod._now_jst().replace(tzinfo=None).isoformat(timespec="seconds") == "2026-08-08T01:02:00"


def test_aggregate_start_prediction_metrics_today_uses_jst(monkeypatch):
    mod = _load("aggregate_start_prediction_metrics", "scripts/aggregate_start_prediction_metrics.py")
    fake_now = datetime(2026, 8, 8, 1, 4, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_db_size_check_uses_jst(monkeypatch):
    mod = _load("db_size_check", "scripts/db_size_check.py")
    fake_now = datetime(2026, 8, 8, 1, 6, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"
    assert mod._now_jst().replace(tzinfo=None).isoformat(timespec="seconds") == "2026-08-08T01:06:00"


def test_rebuild_racer_accident_stats_today_uses_jst(monkeypatch):
    mod = _load("rebuild_racer_accident_stats", "scripts/rebuild_racer_accident_stats.py")
    fake_now = datetime(2026, 8, 8, 1, 8, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_time_split_validate_today_uses_jst(monkeypatch):
    mod = _load("time_split_validate", "scripts/time_split_validate.py")
    fake_now = datetime(2026, 8, 8, 1, 10, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_update_grades_from_lzh_today_uses_jst(monkeypatch):
    mod = _load("update_grades_from_lzh", "scripts/update_grades_from_lzh.py")
    fake_now = datetime(2026, 8, 8, 1, 12, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"


def test_ingest_fan_handbook_today_uses_jst(monkeypatch):
    mod = _load("ingest_fan_handbook", "scripts/ingest_fan_handbook.py")
    fake_now = datetime(2026, 8, 8, 1, 14, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"
