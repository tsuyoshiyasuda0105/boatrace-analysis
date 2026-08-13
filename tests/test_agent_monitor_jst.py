import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_agent_monitor():
    spec = importlib.util.spec_from_file_location(
        "agent_monitor",
        ROOT / "scripts" / "agent_monitor.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_agent_monitor_normalizes_naive_task_log_values_to_jst():
    mod = _load_agent_monitor()
    naive = datetime(2026, 8, 8, 6, 30, 0)
    aware = mod._normalize_jst_datetime(naive)

    assert aware is not None
    assert aware.tzinfo is not None
    assert aware.utcoffset() == mod.JST.utcoffset(aware)


def test_agent_monitor_task_check_accepts_existing_naive_success(monkeypatch):
    mod = _load_agent_monitor()
    monkeypatch.setattr(mod, "NOW", datetime(2026, 8, 8, 10, 0, 0, tzinfo=mod.JST))
    monkeypatch.setattr(mod, "TODAY", mod.NOW.date())
    monkeypatch.setattr(mod, "_render_primary_mode", lambda: False)
    monkeypatch.setattr(mod, "_pc_schedule_paused", lambda: False)
    monkeypatch.setattr(
        mod.task_log,
        "last_success_at",
        lambda *_args, **_kwargs: datetime(2026, 8, 8, 9, 30, 0),
    )

    status, message = mod.check_task(
        {"name": "poll_results", "label": "結果ポーリング", "ok_h": 1, "warn_h": 2, "active": (8.5, 23)}
    )

    assert status == "ok"
    assert "0.5h前に成功" in message
