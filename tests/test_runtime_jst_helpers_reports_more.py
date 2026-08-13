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


def test_search_strategies_now_uses_jst(monkeypatch):
    mod = _load("search_strategies_runtime_test", "scripts/search_strategies.py")
    fake_now = datetime(2026, 8, 8, 9, 0, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._now_jst() == fake_now


def test_explore_auto_loop_now_uses_jst(monkeypatch):
    mod = _load("explore_auto_loop_runtime_test", "scripts/explore_auto_loop.py")
    fake_now = datetime(2026, 8, 8, 9, 5, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._now_jst() == fake_now


def test_analyze_reports_now_use_jst(monkeypatch):
    boat4 = _load("analyze_boat4_kado_runtime_test", "scripts/analyze_boat4_kado.py")
    onefour = _load("analyze_14_41_runtime_test", "scripts/analyze_14_41.py")

    fake_now = datetime(2026, 8, 8, 9, 10, tzinfo=boat4.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz in {boat4.JST, onefour.JST}
            return fake_now

    monkeypatch.setattr(boat4, "datetime", _FakeDatetime)
    monkeypatch.setattr(onefour, "datetime", _FakeDatetime)

    assert boat4._now_jst() == fake_now
    assert onefour._now_jst() == fake_now


def test_time_split_validate_now_uses_jst(monkeypatch):
    mod = _load("time_split_validate_runtime_test_2", "scripts/time_split_validate.py")
    fake_now = datetime(2026, 8, 8, 9, 15, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._now_jst() == fake_now
