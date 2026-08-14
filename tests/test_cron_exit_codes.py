"""P0-2 タスク2: cron 終了コードの正直化の回帰テスト。

「まだリトライが残っている失敗 = exit 0 / もうリトライが無い最終失敗 = exit 非0」
- maintenance: 自動窓 (04:00-07:00) の最終 tick で未完フェーズあり → 非0
- bootstrap: 07:30 の ALERT 発火時点でソース未解決 → 非0
"""
from datetime import datetime

import pytest

from scripts import render_maintenance_scheduler as maintenance
from scripts import render_program_bootstrap_scheduler as bootstrap


def _jst(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 14, hour, minute, tzinfo=maintenance.JST)


# --- maintenance: is_final_window_tick -------------------------------------

def test_final_window_tick_detection():
    assert maintenance.is_final_window_tick(_jst(6, 50)) is True
    assert maintenance.is_final_window_tick(_jst(6, 55)) is True
    assert maintenance.is_final_window_tick(_jst(6, 40)) is False
    assert maintenance.is_final_window_tick(_jst(4, 0)) is False
    assert maintenance.is_final_window_tick(_jst(7, 0)) is False
    assert maintenance.is_final_window_tick(_jst(3, 59)) is False


# --- maintenance: main() exit codes ----------------------------------------

@pytest.fixture
def maintenance_main(monkeypatch):
    """main() の依存を差し替え、(tick結果, 現在時刻) を注入できるようにする。"""
    calls = {"notify": [], "status": []}
    monkeypatch.setattr(maintenance, "log_deploy_revision", lambda *_a, **_k: None)
    monkeypatch.setattr(
        maintenance,
        "notify_cron_failure",
        lambda job, message, **kwargs: calls["notify"].append((job, message, kwargs)),
    )
    monkeypatch.setattr(
        maintenance,
        "_write_window_status",
        lambda run_date, status, message, detail: calls["status"].append(
            (run_date, status, message, detail)
        ),
    )
    monkeypatch.setattr("sys.argv", ["render_maintenance_scheduler.py"])

    def run(now, tick_result):
        monkeypatch.setattr(maintenance, "jst_now", lambda: now)
        monkeypatch.setattr(maintenance, "run_tick", lambda _now, **_k: tick_result)
        return maintenance.main()

    return run, calls


def test_maintenance_midwindow_degraded_stays_zero(maintenance_main):
    run, calls = maintenance_main
    result = {"status": "degraded", "incomplete_phases": ["detail"], "date": "2026-08-14"}
    assert run(_jst(5, 50), result) == 0
    assert calls["notify"] == []
    assert calls["status"] == []


def test_maintenance_final_tick_degraded_exits_nonzero(maintenance_main):
    run, calls = maintenance_main
    result = {
        "status": "degraded",
        "incomplete_phases": ["detail", "integrity"],
        "date": "2026-08-14",
    }
    assert run(_jst(6, 50), result) == 1
    # system_status に error を記録
    assert calls["status"][0][1] == "error"
    assert "detail" in calls["status"][0][2]
    # 管理者宛メール通知 (最終失敗のみ)
    assert len(calls["notify"]) == 1
    assert calls["notify"][0][0] == "boatrace-race-detail-cron"


def test_maintenance_final_tick_ready_is_zero(maintenance_main):
    run, calls = maintenance_main
    assert run(_jst(6, 50), {"status": "ready", "date": "2026-08-14"}) == 0
    assert calls["notify"] == []


def test_maintenance_final_tick_lock_noop_is_zero(maintenance_main):
    run, calls = maintenance_main
    result = {"status": "noop", "reason": "previous-run-active", "date": "2026-08-14"}
    assert run(_jst(6, 50), result) == 0
    assert calls["notify"] == []


def test_maintenance_final_tick_waiting_recomputes_incomplete(maintenance_main, monkeypatch):
    run, calls = maintenance_main
    # waiting は incomplete_phases を返さないため task_runs から再判定する
    monkeypatch.setattr(
        maintenance, "phase_success", lambda phase, _date: phase == "accident"
    )
    result = {"status": "waiting", "phase": "program", "date": "2026-08-14"}
    assert run(_jst(6, 50), result) == 1
    assert "program" in calls["status"][0][3]["incomplete_phases"]
    assert "accident" not in calls["status"][0][3]["incomplete_phases"]


def test_maintenance_notify_failure_does_not_mask_exit_code(maintenance_main, monkeypatch):
    run, calls = maintenance_main
    monkeypatch.setattr(
        maintenance,
        "notify_cron_failure",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("mail down")),
    )
    result = {"status": "degraded", "incomplete_phases": ["detail"], "date": "2026-08-14"}
    assert run(_jst(6, 50), result) == 1


# --- bootstrap: main() exit codes ------------------------------------------

@pytest.fixture
def bootstrap_main(monkeypatch):
    calls = {"notify": []}
    monkeypatch.setattr(bootstrap, "log_deploy_revision", lambda *_a, **_k: None)
    monkeypatch.setattr(
        bootstrap,
        "notify_cron_failure",
        lambda job, message, **kwargs: calls["notify"].append((job, message, kwargs)),
    )

    def run(tick_result):
        monkeypatch.setattr(bootstrap, "run_tick", lambda _now: tick_result)
        return bootstrap.main(["--now", "2026-08-14T07:30:00"])

    return run, calls


def test_bootstrap_waiting_without_final_alert_is_zero(bootstrap_main):
    run, calls = bootstrap_main
    result = {"status": "waiting", "alert_due": False, "alert_status": None}
    assert run(result) == 0
    assert calls["notify"] == []


def test_bootstrap_unresolved_at_0730_exits_nonzero(bootstrap_main):
    run, calls = bootstrap_main
    result = {
        "status": "waiting",
        "date": "2026-08-14",
        "alert_due": True,
        "alert_status": "error",
        "official_ready": False,
        "openapi_ready": False,
        "gate_ready": False,
    }
    assert run(result) == 1
    assert len(calls["notify"]) == 1
    assert calls["notify"][0][0] == "boatrace-program-bootstrap-cron"


def test_bootstrap_resolved_alert_is_zero(bootstrap_main):
    run, calls = bootstrap_main
    result = {"status": "ready", "alert_due": True, "alert_status": "ok"}
    assert run(result) == 0
    assert calls["notify"] == []


# --- bootstrap: run_tick が alert_status を返す ------------------------------

def test_run_tick_reports_alert_status_error_when_unresolved(monkeypatch):
    from contextlib import contextmanager

    state = {}
    statuses = []
    monkeypatch.setattr(bootstrap, "assert_safe_production_write", lambda **_k: None)
    monkeypatch.setattr(bootstrap, "_ensure_tables", lambda: None)

    @contextmanager
    def unlocked():
        yield True

    monkeypatch.setattr(bootstrap, "_run_lock", unlocked)
    monkeypatch.setattr(
        bootstrap,
        "_load_task",
        lambda task, target: state.get(
            (task, target), {"status": "missing", "run_count": 0, "detail": {}}
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "_write_task",
        lambda task, target, status, detail, **_k: state.__setitem__(
            (task, target), {"status": status, "run_count": 1, "detail": detail}
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "_write_status",
        lambda target, status, message, detail: statuses.append((status, message)),
    )
    monkeypatch.setattr(bootstrap, "collect_official", lambda *_a: False)
    monkeypatch.setattr(bootstrap, "collect_openapi", lambda *_a: False)

    now = datetime(2026, 8, 14, 7, 30, tzinfo=bootstrap.JST)
    result = bootstrap.run_tick(now)

    assert result["alert_status"] == "error"
    assert statuses[-1][0] == "error"
    # 2回目の tick では alert は再発火しない (alert_status は None)
    second = bootstrap.run_tick(datetime(2026, 8, 14, 7, 35, tzinfo=bootstrap.JST))
    assert second["alert_status"] is None
