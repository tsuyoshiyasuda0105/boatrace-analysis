"""起動時タスクキャッチアップの回帰テスト。

- src/db/task_log.py: 実行記録 (success_at は失敗で消えない 等)
- scripts/startup_catchup.py: needs_catchup の 3 戦略 (daily_once / windows / interval)

needs_catchup は task_log.last_success_at を参照するため、時刻依存テストでは
last_success_at を monkeypatch して実時計から切り離す (CI のどの時刻でも安定)。
"""
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_startup_catchup():
    spec = importlib.util.spec_from_file_location(
        "startup_catchup", ROOT / "scripts" / "startup_catchup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_task_log_record_and_query(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    from src.db import task_log

    # 未記録なら None
    assert task_log.last_success_at("daily_collect") is None
    assert task_log.get_today("daily_collect") is None

    # 成功記録 → success_at が入る / run_count=1
    task_log.record("daily_collect", "success")
    assert task_log.last_success_at("daily_collect") is not None
    row = task_log.get_today("daily_collect")
    assert row["status"] == "success"
    assert row["run_count"] == 1

    # 失敗を重ねても success_at は消えない / run_count=2
    task_log.record("daily_collect", "failure")
    assert task_log.last_success_at("daily_collect") is not None
    row = task_log.get_today("daily_collect")
    assert row["run_count"] == 2
    assert row["status"] == "failure"


def test_needs_catchup_daily_once(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    from src.db import task_log
    sc = _load_startup_catchup()

    task = {"name": "daily_collect", "strategy": "daily_once", "times": ["06:00"]}
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

    # 予定後・未実行 → 要
    assert sc.needs_catchup(task, base)[0] is True
    # 予定前 → 不要
    assert sc.needs_catchup(task, base.replace(hour=5))[0] is False
    # 成功記録後 → 不要 (時刻比較しないので CI 時計に非依存)
    task_log.record("daily_collect", "success")
    assert sc.needs_catchup(task, base)[0] is False


def test_needs_catchup_windows(monkeypatch):
    sc = _load_startup_catchup()
    from src.db import task_log
    task = {"name": "hourly", "strategy": "windows",
            "times": ["09:00", "11:00", "13:00"]}
    base = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    # 未取得 → 要
    monkeypatch.setattr(task_log, "last_success_at", lambda *a, **k: None)
    assert sc.needs_catchup(task, base)[0] is True
    # 10:00 成功 (直近枠 11:00 より前) → 要
    monkeypatch.setattr(task_log, "last_success_at", lambda *a, **k: base.replace(hour=10))
    assert sc.needs_catchup(task, base)[0] is True
    # 11:30 成功 (直近枠 11:00 より後) → 不要
    monkeypatch.setattr(task_log, "last_success_at",
                        lambda *a, **k: base.replace(hour=11, minute=30))
    assert sc.needs_catchup(task, base)[0] is False
    # 全枠前 (08:00) → 不要
    monkeypatch.setattr(task_log, "last_success_at", lambda *a, **k: None)
    assert sc.needs_catchup(task, base.replace(hour=8))[0] is False


def test_needs_catchup_interval(monkeypatch):
    sc = _load_startup_catchup()
    from src.db import task_log
    task = {"name": "poll_results", "strategy": "interval",
            "active": ["08:30", "23:00"], "stale_min": 15}
    base = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    # 稼働時間外 (02:00) → 不要
    monkeypatch.setattr(task_log, "last_success_at", lambda *a, **k: None)
    assert sc.needs_catchup(task, base.replace(hour=2))[0] is False
    # 稼働中・未取得 → 要
    assert sc.needs_catchup(task, base)[0] is True
    # 5 分前に取得 → 不要
    monkeypatch.setattr(task_log, "last_success_at", lambda *a, **k: base - timedelta(minutes=5))
    assert sc.needs_catchup(task, base)[0] is False
    # 30 分前 (stale_min=15 超) → 鮮度切れ → 要
    monkeypatch.setattr(task_log, "last_success_at", lambda *a, **k: base - timedelta(minutes=30))
    assert sc.needs_catchup(task, base)[0] is True
