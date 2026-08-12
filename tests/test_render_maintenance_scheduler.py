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


def test_tick_runs_only_first_due_incomplete_phase(monkeypatch):
    calls = []
    records = []
    monkeypatch.setattr(scheduler, "maintenance_lock", _locked)
    monkeypatch.setattr(scheduler, "phase_success", lambda phase, _date: phase == "accident")
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
