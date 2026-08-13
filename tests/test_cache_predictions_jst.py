import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_cache_predictions():
    spec = importlib.util.spec_from_file_location(
        "cache_predictions",
        ROOT / "scripts" / "cache_predictions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cache_predictions_today_uses_jst(monkeypatch):
    mod = _load_cache_predictions()
    fake_now = datetime(2026, 8, 8, 0, 5, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"
    assert mod._now_jst_iso() == "2026-08-08T00:05:00"
