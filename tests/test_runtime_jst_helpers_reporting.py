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


def test_start_prediction_features_safe_cutoff_uses_jst(monkeypatch):
    mod = _load("start_prediction_features_runtime_test", "src/start_prediction/features.py")
    fake_now = datetime(2026, 8, 8, 8, 15, 0, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    got = mod._safe_cutoff("pre_exhibition", {"race_closed_at": None}, [])
    assert got == "2026-08-08T08:15:00+09:00"


def test_verification_report_now_uses_jst(monkeypatch):
    mod = _load("verification_report_runtime_test", "src/verification/report.py")
    fake_now = datetime(2026, 8, 8, 8, 20, 0, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._now_jst() == fake_now
