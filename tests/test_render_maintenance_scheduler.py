from contextlib import contextmanager
from datetime import datetime

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

    result = scheduler.run_tick(_now(6, 40))

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

    result = scheduler.run_tick(_now(6, 40))

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


def test_integrity_phase_reconciles_roi_and_allows_persisted_warnings(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "phase_success", lambda *_args: False)
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
    assert "--warnings-ok" in calls[-1]
