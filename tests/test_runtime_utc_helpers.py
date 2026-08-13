import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_subscribers_utc_iso_uses_explicit_utc(monkeypatch):
    mod = _load("subscribers_runtime_test", "src/notifications/subscribers.py")
    fake_now = datetime(2026, 8, 8, 7, 30, 45, 123456, tzinfo=timezone.utc)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._utc_iso() == "2026-08-08T07:30:45+00:00"


def test_odds_recorded_at_uses_explicit_utc(monkeypatch):
    mod = _load("odds_runtime_test", "src/collectors/odds.py")
    fake_now = datetime(2026, 8, 8, 7, 31, 46, 999999, tzinfo=timezone.utc)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._utc_now_iso() == "2026-08-08T07:31:46+00:00"


def test_value_bet_detected_at_uses_explicit_utc(monkeypatch):
    mod = _load("value_bet_runtime_test", "src/evaluation/value_bet.py")
    fake_now = datetime(2026, 8, 8, 7, 32, 47, 654321, tzinfo=timezone.utc)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._utc_now_iso() == "2026-08-08T07:32:47+00:00"
