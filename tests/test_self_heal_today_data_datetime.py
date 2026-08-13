import importlib.util
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_self_heal():
    spec = importlib.util.spec_from_file_location(
        "self_heal_today_data",
        ROOT / "scripts" / "self_heal_today_data.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_jst_datetime_normalizes_naive_and_aware_values():
    mod = _load_self_heal()

    naive = mod._parse_jst_datetime("2026-08-08T16:00:00")
    aware = mod._parse_jst_datetime("2026-08-08T07:00:00+00:00")

    assert naive is not None
    assert aware is not None
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    assert naive.utcoffset() == aware.utcoffset()
    assert naive.hour == 16
    assert aware.hour == 16


def test_recent_attempt_exists_accepts_existing_naive_finished_at(monkeypatch):
    mod = _load_self_heal()
    now = datetime(2026, 8, 8, 16, 30, tzinfo=mod.JST)

    monkeypatch.setattr(
        mod.task_log,
        "get_today",
        lambda _task_name: {"finished_at": "2026-08-08T16:10:00"},
    )

    assert mod._recent_attempt_exists(now) is True


def test_today_iso_uses_jst_date(monkeypatch):
    mod = _load_self_heal()
    fake_now = datetime(2026, 8, 8, 0, 15, tzinfo=mod.JST)

    monkeypatch.setattr(mod, "_now", lambda: fake_now)

    assert mod._today_iso() == date(2026, 8, 8).isoformat()
