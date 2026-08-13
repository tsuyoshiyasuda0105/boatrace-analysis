import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_cache_predictions_today_uses_jst(monkeypatch):
    mod = _load("render_cache_predictions", "scripts/render_cache_predictions.py")
    fake_now = datetime(2026, 8, 8, 0, 12, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst().isoformat() == "2026-08-08"
    assert mod._now_jst_iso() == "2026-08-08T00:12:00"


def test_prewarm_race_detail_tags_today_uses_jst(monkeypatch):
    mod = _load("prewarm_race_detail_tags", "scripts/prewarm_race_detail_tags.py")
    fake_now = datetime(2026, 8, 8, 0, 18, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"


def test_evaluate_start_predictions_today_uses_jst(monkeypatch):
    mod = _load("evaluate_start_predictions", "scripts/evaluate_start_predictions.py")
    fake_now = datetime(2026, 8, 8, 0, 22, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"


def test_refresh_race_weather_today_uses_jst(monkeypatch):
    mod = _load("refresh_race_weather", "scripts/refresh_race_weather.py")
    fake_now = datetime(2026, 8, 8, 0, 26, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today() == "2026-08-08"
    assert mod._now_jst() == fake_now


def test_generate_start_predictions_today_uses_jst(monkeypatch):
    mod = _load("generate_start_predictions", "scripts/generate_start_predictions.py")
    fake_now = datetime(2026, 8, 8, 0, 28, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"
