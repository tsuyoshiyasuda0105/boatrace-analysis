from contextlib import contextmanager
from datetime import datetime
import json

import pytest

from scripts import render_maintenance_scheduler as scheduler


def _now(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 13, hour, minute, tzinfo=scheduler.JST)


@contextmanager
def _locked():
    yield True


def test_tick_is_idle_outside_maintenance_window():
    assert scheduler.run_tick(_now(3, 59))["reason"] == "outside-maintenance-window"
    assert scheduler.run_tick(_now(7, 0))["reason"] == "outside-maintenance-window"


def test_manual_recovery_is_bounded_and_runs_after_automatic_window(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda phase, _date: phase == "accident")
    monkeypatch.setattr(scheduler, "phase_attempts", lambda *_args: 0)
    monkeypatch.setattr(
        scheduler,
        "RUNNERS",
        {phase: (lambda _now, phase=phase: calls.append(phase) or (True, {})) for phase, _ in scheduler.PHASES},
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *_args: None)

    result = scheduler.run_tick(_now(8, 0), allow_recovery=True)

    assert result["phase"] == "program"
    assert calls == ["program"]
    assert scheduler.run_tick(_now(12, 0), allow_recovery=True)["reason"] == "outside-maintenance-window"


def test_phase_attempts_ignores_legacy_scheduler_failures(monkeypatch):
    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return self

        def fetchone(self):
            return (18, '{"target_date":"2026-08-12"}')

    monkeypatch.setattr(scheduler, "db_connect", _Connection)

    assert scheduler.phase_attempts("accident", "2026-08-13") == 0


def test_phase_attempts_uses_current_scheduler_failures(monkeypatch):
    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return self

        def fetchone(self):
            return (19, '{"scheduler_version":"v2","attempt_count":2}')

    monkeypatch.setattr(scheduler, "db_connect", _Connection)

    assert scheduler.phase_attempts("accident", "2026-08-13") == 2


def test_recorded_phase_uses_new_attempt_count_not_legacy_run_count(monkeypatch):
    records = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(scheduler, "phase_attempts", lambda *_args: 0)
    monkeypatch.setattr(
        scheduler,
        "RUNNERS",
        {**scheduler.RUNNERS, "accident": lambda _now: (False, {"reason": "upstream"})},
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *args: records.append(args))

    scheduler.run_tick(_now(4, 10))

    assert records[0][3]["attempt_count"] == 1


def test_tick_runs_only_first_due_incomplete_phase(monkeypatch):
    calls = []
    records = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda phase, _date: phase == "accident")
    monkeypatch.setattr(scheduler, "phase_attempts", lambda *_args: 0)
    monkeypatch.setattr(
        scheduler,
        "RUNNERS",
        {phase: (lambda _now, phase=phase: calls.append(phase) or (True, {})) for phase, _ in scheduler.PHASES},
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *args: records.append(args))

    result = scheduler.run_tick(_now(6, 30))

    assert result["phase"] == "program"
    assert calls == ["program"]
    assert records[0][0:3] == ("program", "2026-08-13", True)


def test_failed_phase_is_recorded_for_next_tick_retry(monkeypatch):
    records = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(scheduler, "phase_attempts", lambda *_args: 0)
    monkeypatch.setattr(
        scheduler,
        "RUNNERS",
        {**scheduler.RUNNERS, "accident": lambda _now: (False, {"reason": "source-late"})},
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *args: records.append(args))

    result = scheduler.run_tick(_now(4, 10))

    assert result["status"] == "waiting"
    assert result["phase"] == "accident"
    assert records[0][2] is False


def test_tick_does_not_start_when_previous_run_holds_lock(monkeypatch):
    @contextmanager
    def unlocked():
        yield False

    monkeypatch.setattr(scheduler, "maintenance_lock", unlocked)
    result = scheduler.run_tick(_now(5, 0))
    assert result["reason"] == "previous-run-active"


def test_accident_circuit_does_not_block_program(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(
        scheduler,
        "phase_attempts",
        lambda phase, _date: scheduler.MAX_PHASE_ATTEMPTS if phase == "accident" else 0,
    )
    monkeypatch.setattr(
        scheduler,
        "RUNNERS",
        {phase: (lambda _now, phase=phase: calls.append(phase) or (True, {})) for phase, _ in scheduler.PHASES},
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *_args: None)

    result = scheduler.run_tick(_now(4, 40))

    assert result["phase"] == "program"
    assert calls == ["program"]


def test_required_dependency_still_blocks_downstream(monkeypatch):
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(
        scheduler,
        "phase_attempts",
        lambda phase, _date: scheduler.MAX_PHASE_ATTEMPTS if phase in {"accident", "program"} else 0,
    )

    result = scheduler.run_tick(_now(6, 30))

    assert result["status"] == "degraded"
    assert "program" in result["incomplete_phases"]


def test_accident_phase_resumes_from_failed_snapshot(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "phase_success",
        lambda phase, _date: phase == "accident_rebuild",
    )
    monkeypatch.setattr(scheduler.regular, "latest_completed_results_date", lambda: "2026-08-12")
    monkeypatch.setattr(
        scheduler.regular,
        "run_accident_rebuild",
        lambda *_args: (_ for _ in ()).throw(AssertionError("rebuild must be skipped")),
    )
    monkeypatch.setattr(
        scheduler.regular,
        "run_accident_rank_snapshot",
        lambda target: calls.append(("snapshot", target)) or True,
    )
    monkeypatch.setattr(scheduler.regular, "run_py", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scheduler, "record_phase", lambda phase, *_args: calls.append(("record", phase)))

    ok, detail = scheduler.run_accident_phase(_now(4, 20))

    assert ok is True
    assert detail["rebuild_ok"] is True
    assert calls[0] == ("snapshot", "2026-08-12")


def test_accident_phase_keeps_previous_day_after_live_results_begin(monkeypatch):
    targets = []
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(
        scheduler.regular,
        "latest_completed_results_date",
        lambda: "2026-08-13",
    )
    monkeypatch.setattr(scheduler.regular, "run_accident_rebuild", lambda *_args: True)
    monkeypatch.setattr(
        scheduler.regular,
        "run_accident_rank_snapshot",
        lambda target: targets.append(target) or True,
    )
    monkeypatch.setattr(scheduler.regular, "run_py", lambda args, **_kwargs: targets.append(args[2]) or True)
    monkeypatch.setattr(scheduler, "record_phase", lambda *_args: None)

    ok, detail = scheduler.run_accident_phase(_now(9, 0))

    assert ok is True
    assert detail["target_date"] == "2026-08-12"
    assert targets == ["2026-08-12", "2026-08-12"]


def test_detail_phase_finishes_pages_and_accepts_new_motor_warnings(monkeypatch):
    calls = []

    def fake_run_py(args, **_kwargs):
        calls.append(tuple(args))
        if args[0] == "scripts/prewarm_race_detail_tags.py":
            return scheduler.regular.PyRunResult(7, "tag warning")
        return scheduler.regular.PyRunResult(0)

    monkeypatch.setattr(scheduler.regular, "run_py_detailed", fake_run_py)
    monkeypatch.setattr(scheduler, "_child_peak_rss_mb", lambda: 123.4)
    monkeypatch.setattr(
        scheduler.regular,
        "race_detail_page_cache_coverage",
        lambda _date: {"races": 192, "covered": 192},
    )

    ok, detail = scheduler.run_detail_phase(_now(6, 0))

    assert ok is True
    assert {key: value for key, value in detail.items() if key != "subprocesses"} == {
        "date": "2026-08-13", "tags_ok": False, "pages_ok": True,
        "integrity_ok": True, "partial": False, "remaining": 0,
    }
    assert detail["subprocesses"]["tags"] == {
        "return_code": 7,
        "timed_out": False,
        "oom_suspected": False,
        "stderr_tail": "tag warning",
        "stdout_tail": "",
        "peak_rss_mb": 123.4,
    }
    assert [call[0] for call in calls] == [
        "scripts/prewarm_race_detail_tags.py",
        "scripts/prewarm_race_detail_pages.py",
        "scripts/check_post_run_integrity.py",
    ]
    assert "--warnings-ok" in calls[-1]
    assert ("--scope", "detail_rows") == calls[-1][3:5]
    assert ("--scope", "motor_cache") == calls[-1][5:7]
    assert ("--scope", "detail_cache") == calls[-1][7:9]


def test_detail_phase_accepts_budgeted_partial_and_still_runs_pages(monkeypatch):
    calls = []

    def fake_run_py(args, **_kwargs):
        calls.append(tuple(args))
        return scheduler.regular.PyRunResult(
            0 if args[0] != "scripts/check_post_run_integrity.py" else 1
        )

    monkeypatch.setattr(scheduler.regular, "run_py_detailed", fake_run_py)
    monkeypatch.setattr(
        scheduler.regular,
        "race_detail_page_cache_coverage",
        lambda _date: {"races": 192, "covered": 80},
    )

    ok, detail = scheduler.run_detail_phase(_now(6, 0))

    assert ok is True
    assert detail["partial"] is True
    assert detail["remaining"] == 112
    assert [call[0] for call in calls] == [
        "scripts/prewarm_race_detail_tags.py",
        "scripts/prewarm_race_detail_pages.py",
        "scripts/check_post_run_integrity.py",
    ]
    assert "--budget-sec" in calls[0]
    assert "--missing-only" in calls[1]


def test_detail_phase_failure_diagnostics_are_recorded_as_task_json(monkeypatch):
    results = iter(
        [
            scheduler.regular.PyRunResult(137, "x" * 600 + "tags killed"),
            scheduler.regular.PyRunResult(None, "pages timed out", timed_out=True),
            scheduler.regular.PyRunResult(9, "integrity failed"),
        ]
    )
    recorded = []
    monkeypatch.setattr(
        scheduler.regular,
        "run_py_detailed",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(scheduler, "_child_peak_rss_mb", lambda: 511.8)
    monkeypatch.setattr(
        scheduler.regular,
        "race_detail_page_cache_coverage",
        lambda _date: {"races": 144, "covered": 0},
    )
    monkeypatch.setattr(
        scheduler.regular,
        "record_task",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    ok, detail = scheduler.run_detail_phase(_now(6, 0))
    scheduler.record_phase("detail", "2026-08-13", ok, detail)

    assert ok is False
    stored = json.loads(recorded[-1][1]["detail"])
    assert recorded[-1][0][:3] == (
        "render_maintenance_detail_v1", "2026-08-13", "failure"
    )
    assert stored["remaining"] == 144
    assert set(stored["subprocesses"]) == {"tags", "pages", "integrity"}
    assert stored["subprocesses"]["tags"]["return_code"] == 137
    assert stored["subprocesses"]["tags"]["oom_suspected"] is True
    assert stored["subprocesses"]["tags"]["stderr_tail"].endswith("tags killed")
    assert len(stored["subprocesses"]["tags"]["stderr_tail"]) == 500
    assert stored["subprocesses"]["pages"]["timed_out"] is True
    assert stored["subprocesses"]["integrity"]["peak_rss_mb"] == 511.8


def test_detail_subprocess_spawn_error_is_retained(monkeypatch):
    monkeypatch.setattr(
        scheduler.regular,
        "run_py_detailed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cannot allocate memory")),
    )
    monkeypatch.setattr(scheduler, "_child_peak_rss_mb", lambda: None)

    ok, diagnostic = scheduler._run_detail_subprocess(["child.py"], timeout=10)

    assert ok is False
    assert diagnostic["return_code"] is None
    assert diagnostic["stderr_tail"] == (
        "spawn_error=OSError: cannot allocate memory"
    )
    assert diagnostic["peak_rss_mb"] is None


def test_snapshot_phase_builds_degraded_top_when_signal_refresh_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler.regular,
        "run_signal_refresh_slot",
        lambda *_args, **_kwargs: calls.append("signals") or False,
    )
    monkeypatch.setattr(
        scheduler.regular,
        "run_top_page_snapshot",
        lambda *_args, **kwargs: calls.append(("top", kwargs)) or True,
    )

    ok, detail = scheduler.run_snapshot_phase(_now(6, 20))

    assert ok is False
    assert calls == [
        "signals",
        ("top", {"lightweight": False, "signals_degraded": True}),
    ]
    assert detail == {
        "date": "2026-08-13",
        "signals_ok": False,
        "top_ok": True,
    }


def test_integrity_phase_reconciles_roi_and_allows_persisted_warnings(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(
        scheduler.regular,
        "run_entry_change_snapshots_nonfatal",
        lambda _now: calls.append("entry-change") or {"today": False, "tomorrow": True},
    )
    # 2026-09-03: 逃がし率スナップショット生成も integrity フェーズで走る。
    monkeypatch.setattr(
        scheduler.regular,
        "run_course_role_snapshots_nonfatal",
        lambda _now: calls.append("course-role") or {"today": True, "tomorrow": True},
    )
    monkeypatch.setattr(
        scheduler.regular,
        "run_roi_daily_self_heal",
        lambda _now: calls.append("roi") or True,
    )
    monkeypatch.setattr(
        scheduler.regular,
        "run_py",
        lambda args, **_kwargs: calls.append(tuple(args)) or True,
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *_args: None)

    ok, detail = scheduler.run_integrity_phase(_now(6, 40))

    assert ok is True
    assert detail["roi_ok"] is True
    assert detail["entry_change_snapshots"] == {"today": False, "tomorrow": True}
    assert calls[:3] == ["entry-change", "course-role", "roi"]
    assert "--warnings-ok" in calls[-1]


def _healthy_preflight_measurements() -> dict[str, object]:
    return {
        "races": 2,
        "entries": 12,
        "page_cache_count": 2,
        "motor_cache_count": 12,
        "tag_cache_count": 2,
        "signal_cache_exists": True,
        "signal_cache_pending": False,
        "signal_cache_nonempty": True,
        "signal_count": 1,
        "signal_cache_key": "market_signals:v:test:2026-08-13",
        "today_races_http_status": 200,
        "today_races_candidate_count": 0,
        "accident_snapshot_status": "success",
        "accident_integrity_status": "success",
        "accident_check_status": "ok",
        "accident_check_date": "2026-08-12",
        "backtest_latest_date": "2026-08-12",
        "predictions": 2,
        "race_closed_at_count": 2,
        "open_incidents": 0,
        "cron_failures_12h": 0,
        "healthz_http_status": 200,
        "healthz_body_status": "ok",
        "db_connections": 4,
        "kachisuji_disk_free_bytes": 200 * 1024 * 1024,
        "kachisuji_disk_path": "/data",
    }


@pytest.mark.parametrize(
    ("key", "value", "failed_id"),
    [
        ("entries", 11, 1),
        ("page_cache_count", 1, 2),
        ("motor_cache_count", 11, 3),
        ("tag_cache_count", 1, 4),
        ("today_races_http_status", 500, 5),
        ("accident_check_status", "warning", 6),
        ("backtest_latest_date", "2026-08-11", 7),
        ("predictions", 1, 8),
        ("signal_cache_nonempty", False, 9),
        ("race_closed_at_count", 1, 10),
        ("open_incidents", 1, 11),
        ("healthz_http_status", 503, 12),
        ("db_connections", 45, 13),
        ("kachisuji_disk_free_bytes", 99 * 1024 * 1024, 14),
    ],
)
def test_each_preflight_check_has_an_independent_fail_decision(key, value, failed_id):
    measurements = _healthy_preflight_measurements()
    measurements[key] = value

    checks = scheduler.evaluate_preflight_checks(
        measurements,
        target_date="2026-08-13",
        yesterday="2026-08-12",
    )

    assert len(checks) == 14
    assert next(item for item in checks if item["id"] == failed_id)["status"] == "fail"


def test_all_fourteen_preflight_checks_pass_with_complete_measurements():
    checks = scheduler.evaluate_preflight_checks(
        _healthy_preflight_measurements(),
        target_date="2026-08-13",
        yesterday="2026-08-12",
    )

    assert [item["id"] for item in checks] == list(range(1, 15))
    assert all(item["ok"] for item in checks)
    assert {item["id"] for item in checks if item["critical"]} == {1, 2, 5}


def test_preflight_measurements_use_mocked_database(monkeypatch):
    class _Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=()):
            normalized = " ".join(sql.split())
            if "SELECT (SELECT COUNT(*) FROM races" in normalized:
                return _Cursor([(2, 12, 2, 2)])
            if normalized.startswith("SELECT race_id FROM races"):
                return _Cursor([("r1",), ("r2",)])
            if normalized.startswith("SELECT status FROM task_runs"):
                return _Cursor([("success",)])
            if "check_name = 'post_run_accident'" in normalized:
                return _Cursor([("2026-08-12", "ok")])
            if "FROM incident_log" in normalized:
                return _Cursor([(0,)])
            if "task_name LIKE 'render_%'" in normalized:
                return _Cursor([(0,)])
            if "FROM pg_stat_activity" in normalized:
                return _Cursor([(4,)])
            raise AssertionError(normalized)

    counts = iter([2, 2, 12])
    monkeypatch.setattr(scheduler, "db_connect", _Connection)
    monkeypatch.setattr(scheduler, "_count_cache_keys", lambda *_args: next(counts))
    monkeypatch.setattr(
        scheduler,
        "_load_signal_cache_measurement",
        lambda *_args: {
            "signal_cache_exists": True,
            "signal_cache_pending": False,
            "signal_cache_nonempty": True,
            "signal_count": 1,
            "signal_cache_key": "signal-key",
        },
    )
    monkeypatch.setattr(
        scheduler,
        "_kachisuji_latest_date",
        lambda: {"backtest_latest_date": "2026-08-12"},
    )
    monkeypatch.setattr(
        scheduler,
        "_kachisuji_disk_space",
        lambda: {
            "kachisuji_disk_free_bytes": 200 * 1024 * 1024,
            "kachisuji_disk_path": "/data",
        },
    )
    monkeypatch.setattr(
        scheduler,
        "_probe_today_races_page",
        lambda _date: {"today_races_http_status": 200, "today_races_candidate_count": 1},
    )
    monkeypatch.setattr(
        scheduler,
        "_probe_healthz",
        lambda: {"healthz_http_status": 200, "healthz_body_status": "ok"},
    )

    measurements = scheduler.collect_preflight_measurements(_now(6, 40))
    checks = scheduler.evaluate_preflight_checks(
        measurements,
        target_date="2026-08-13",
        yesterday="2026-08-12",
    )

    assert all(item["ok"] for item in checks)


def test_preflight_critical_failure_repairs_once_then_extends_gate(monkeypatch):
    measurements = _healthy_preflight_measurements()
    measurements["entries"] = 11
    writes = []
    alerts = []
    repairs = []
    monkeypatch.setenv("BOATRACE_PREFLIGHT_GATE", "1")
    monkeypatch.setattr(
        scheduler,
        "_run_preflight_signal_generation",
        lambda _now: (True, {"return_code": 0}),
    )
    monkeypatch.setattr(
        scheduler,
        "collect_preflight_measurements",
        lambda _now: dict(measurements),
    )
    monkeypatch.setattr(
        scheduler,
        "_repair_critical_failures",
        lambda _now, ids: repairs.append(list(ids)) or [
            {"job": "program", "ok": False, "detail": {"return_code": 1}}
        ],
    )
    monkeypatch.setattr(
        scheduler,
        "_write_preflight_status",
        lambda *args: writes.append(args),
    )
    monkeypatch.setattr(
        scheduler,
        "notify_cron_failure",
        lambda *args, **kwargs: alerts.append((args, kwargs)),
    )

    ok, detail = scheduler.run_preflight_phase(_now(6, 40))

    assert ok is True
    assert repairs == [[1]]
    assert detail["summary"]["critical_failed_check_ids"] == [1]
    assert detail["gate"]["extend_maintenance"] is True
    assert detail["gate"]["hard_cap"] == "07:30 JST"
    assert len(writes) == 1
    assert len(alerts) == 1


def test_critical_repair_jobs_are_each_run_at_most_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_program_phase",
        lambda _now: calls.append("program") or (True, {}),
    )
    monkeypatch.setattr(
        scheduler,
        "_run_detail_subprocess",
        lambda *_args, **_kwargs: calls.append("detail_pages") or (True, {}),
    )
    monkeypatch.setattr(
        scheduler,
        "_run_preflight_signal_generation",
        lambda _now: calls.append("today_candidates") or (True, {}),
    )

    repairs = scheduler._repair_critical_failures(_now(6, 40), [1, 2, 5, 1, 2, 5])

    assert calls == ["program", "detail_pages", "today_candidates"]
    assert [item["job"] for item in repairs] == calls


def test_preflight_gate_is_opt_in(monkeypatch):
    monkeypatch.delenv("BOATRACE_PREFLIGHT_GATE", raising=False)
    assert scheduler._preflight_gate_enabled() is False
    monkeypatch.setenv("BOATRACE_PREFLIGHT_GATE", "1")
    assert scheduler._preflight_gate_enabled() is True


def test_preflight_signal_generation_uses_realtime_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler.regular,
        "run_py_detailed",
        lambda args, **kwargs: calls.append((args, kwargs))
        or scheduler.regular.PyRunResult(0),
    )
    monkeypatch.setattr(scheduler, "_child_peak_rss_mb", lambda: None)

    ok, _detail = scheduler._run_preflight_signal_generation(_now(6, 40))

    assert ok is True
    assert calls == [
        (
            [
                "scripts/prewarm_strategy_pages.py",
                "--mode", "realtime",
                "--date", "2026-08-13",
            ],
            {"timeout": 1800},
        )
    ]


def test_0640_tick_prioritizes_preflight_over_late_phase_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
    monkeypatch.setattr(scheduler, "phase_attempts", lambda *_args: 0)
    monkeypatch.setattr(
        scheduler,
        "RUNNERS",
        {
            phase: (lambda _now, phase=phase: calls.append(phase) or (True, {}))
            for phase, _ in scheduler.PHASES
        },
    )
    monkeypatch.setattr(scheduler, "record_phase", lambda *_args: None)

    result = scheduler.run_tick(_now(6, 40))

    assert result["phase"] == "preflight"
    assert calls == ["preflight"]


def test_detail_subprocess_retains_stdout_diagnostics(monkeypatch):
    """prewarm/integrity の診断は stdout に出るので必ず記録する。

    2026-08-16 以降、詳細ページ生成が毎朝 rc=1 で失敗していたのに
    stderr_tail が空で原因を追えなかった実障害の再発防止。
    summary JSON と failures 配列は stdout 側にある。
    """
    summary = (
        '[race-detail-page] summary={"requested_races": 144, "failed": 144, '
        '"failures": [{"race_id": "20260821-01-01", "status": '
        '"persistent_cache_missing"}]}'
    )
    monkeypatch.setattr(
        scheduler.regular,
        "run_py_detailed",
        lambda *_args, **_kwargs: scheduler.regular.PyRunResult(
            1, "", stdout_tail=summary
        ),
    )
    monkeypatch.setattr(scheduler, "_child_peak_rss_mb", lambda: None)

    ok, diagnostic = scheduler._run_detail_subprocess(["child.py"], timeout=10)

    assert ok is False
    assert diagnostic["return_code"] == 1
    assert "persistent_cache_missing" in diagnostic["stdout_tail"]
    assert '"failed": 144' in diagnostic["stdout_tail"]


def test_detail_subprocess_stdout_tail_is_bounded(monkeypatch):
    monkeypatch.setattr(
        scheduler.regular,
        "run_py_detailed",
        lambda *_args, **_kwargs: scheduler.regular.PyRunResult(
            1, "", stdout_tail="x" * 9000
        ),
    )
    monkeypatch.setattr(scheduler, "_child_peak_rss_mb", lambda: None)

    _ok, diagnostic = scheduler._run_detail_subprocess(["child.py"], timeout=10)

    assert len(diagnostic["stdout_tail"]) == 2500
