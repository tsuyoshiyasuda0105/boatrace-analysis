import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_check_post_run_integrity():
    spec = importlib.util.spec_from_file_location(
        "check_post_run_integrity",
        ROOT / "scripts" / "check_post_run_integrity.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_post_run_integrity_today_uses_jst(monkeypatch):
    mod = _load_check_post_run_integrity()
    fake_now = datetime(2026, 8, 8, 0, 10, tzinfo=mod.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == mod.JST
            return fake_now

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    assert mod._today_jst_iso() == "2026-08-08"
