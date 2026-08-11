from contextlib import contextmanager
from datetime import date, datetime, timedelta
import sqlite3

import pytest

from scripts import render_program_bootstrap_scheduler as bootstrap


JST = bootstrap.JST
TARGET = date(2026, 8, 12)


def _now(hour: int, minute: int, day: int = 12) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=JST)


def test_target_window_uses_tomorrow_at_2330_and_today_after_midnight():
    assert bootstrap.target_for_tick(_now(23, 29, day=11)) is None
    assert bootstrap.target_for_tick(_now(23, 30, day=11)) == TARGET
    assert bootstrap.target_for_tick(_now(0, 10)) == TARGET
    assert bootstrap.target_for_tick(_now(9, 55)) == TARGET
    assert bootstrap.target_for_tick(_now(10, 0)) is None


def test_failure_backoff_progresses_15_30_60(monkeypatch):
    writes = []
    monkeypatch.setattr(
        bootstrap,
        "_write_task",
        lambda task, target, status, detail, **_kwargs: writes.append(detail),
    )
    prior = {"detail": {}}
    for expected in (15, 30, 60, 60):
        bootstrap._record_phase_failure(
            bootstrap.OPENAPI_TASK,
            TARGET,
            _now(0, 10),
            prior,
            source_host="openapi.example",
            reason="unavailable",
            missing_stadiums=[1, 2],
        )
        detail = writes[-1]
        assert detail["circuit_open_minutes"] == expected
        assert detail["next_attempt_at"] == (_now(0, 10) + timedelta(minutes=expected)).isoformat(timespec="seconds")
        prior = {"detail": detail}


def test_phase_due_respects_circuit_and_force():
    task = {
        "status": "failure",
        "detail": {"next_attempt_at": _now(1, 10).isoformat(timespec="seconds")},
    }
    assert bootstrap.phase_due(task, _now(1, 0)) is False
    assert bootstrap.phase_due(task, _now(1, 0), force=True) is True
    assert bootstrap.phase_due({"status": "success", "detail": {}}, _now(1, 0), force=True) is False


@pytest.fixture
def in_memory_state(monkeypatch):
    state = {}
    statuses = []

    monkeypatch.setattr(bootstrap, "assert_safe_production_write", lambda **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_ensure_tables", lambda: None)

    @contextmanager
    def unlocked():
        yield True

    monkeypatch.setattr(bootstrap, "_run_lock", unlocked)
    monkeypatch.setattr(
        bootstrap,
        "_load_task",
        lambda task, target: state.get((task, target), {"status": "missing", "run_count": 0, "detail": {}}),
    )

    def write(task, target, status, detail, **_kwargs):
        state[(task, target)] = {"status": status, "run_count": 1, "detail": detail}

    monkeypatch.setattr(bootstrap, "_write_task", write)
    monkeypatch.setattr(
        bootstrap,
        "_write_status",
        lambda target, status, message, detail: statuses.append((target, status, message, detail)),
    )
    return state, statuses


def test_2330_runs_only_official(monkeypatch, in_memory_state):
    state, _statuses = in_memory_state
    calls = []

    def official(target, _now_value, _prior):
        calls.append(("official", target))
        state[(bootstrap.OFFICIAL_TASK, target)] = {"status": "success", "detail": {}}
        return True

    monkeypatch.setattr(bootstrap, "collect_official", official)
    monkeypatch.setattr(bootstrap, "collect_openapi", lambda *_args: calls.append(("openapi", TARGET)))

    result = bootstrap.run_tick(_now(23, 30, day=11))

    assert calls == [("official", TARGET)]
    assert result["official_ready"] is True
    assert result["openapi_ready"] is False


def test_0010_runs_openapi_then_gate(monkeypatch, in_memory_state):
    state, _statuses = in_memory_state
    state[(bootstrap.OFFICIAL_TASK, TARGET)] = {"status": "success", "detail": {}}
    calls = []

    def collect_api(target, _now_value, _prior):
        calls.append("openapi")
        state[(bootstrap.OPENAPI_TASK, target)] = {"status": "success", "detail": {}}
        return True

    def gate(target, _now_value, *, force):
        calls.append(("gate", force))
        state[(bootstrap.GATE_TASK, target)] = {"status": "success", "detail": {}}
        return True

    monkeypatch.setattr(bootstrap, "collect_openapi", collect_api)
    monkeypatch.setattr(bootstrap, "_attempt_gate", gate)

    result = bootstrap.run_tick(_now(0, 10))

    assert calls == ["openapi", ("gate", False)]
    assert result["status"] == "ready"


def test_0630_forces_final_recovery_once(monkeypatch, in_memory_state):
    state, _statuses = in_memory_state
    future = _now(7, 0).isoformat(timespec="seconds")
    state[(bootstrap.OFFICIAL_TASK, TARGET)] = {"status": "failure", "detail": {"next_attempt_at": future}}
    state[(bootstrap.OPENAPI_TASK, TARGET)] = {"status": "failure", "detail": {"next_attempt_at": future}}
    calls = []

    monkeypatch.setattr(bootstrap, "collect_official", lambda *_args: calls.append("official") or False)
    monkeypatch.setattr(bootstrap, "collect_openapi", lambda *_args: calls.append("openapi") or False)

    result = bootstrap.run_tick(_now(6, 30))

    assert calls == ["official", "openapi"]
    assert result["final_recovery"] is True
    assert state[(bootstrap.FINAL_TASK, TARGET)]["status"] == "success"


def test_0730_records_unresolved_admin_error(monkeypatch, in_memory_state):
    _state, statuses = in_memory_state
    monkeypatch.setattr(bootstrap, "collect_official", lambda *_args: False)
    monkeypatch.setattr(bootstrap, "collect_openapi", lambda *_args: False)

    result = bootstrap.run_tick(_now(7, 30))

    assert result["alert_due"] is True
    assert statuses[-1][1:3] == ("error", "program sources unresolved at 07:30 JST")
    second = bootstrap.run_tick(_now(7, 35))
    assert second["alert_due"] is False
    assert len(statuses) == 1


def test_expected_source_waiting_is_a_successful_scheduler_tick(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "run_tick",
        lambda _now: {
            "status": "waiting",
            "final_recovery": True,
            "alert_due": True,
            "gate_ready": False,
        },
    )

    assert bootstrap.main(["--now", "2026-08-12T07:30:00"]) == 0


def test_overlap_is_safe_noop(monkeypatch):
    monkeypatch.setattr(bootstrap, "assert_safe_production_write", lambda **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_ensure_tables", lambda: None)

    @contextmanager
    def locked():
        yield False

    monkeypatch.setattr(bootstrap, "_run_lock", locked)
    result = bootstrap.run_tick(_now(0, 10))
    assert result["reason"] == "previous-run-active"


def test_complete_source_coverage_requires_12_races_and_6_boats():
    official = [
        {
            "stadium_number": 1,
            "race_number": race,
            "boats": [
                {"racer_number": boat, "assigned_motor_number": boat}
                for boat in range(1, 7)
            ],
        }
        for race in range(1, 13)
    ]
    assert bootstrap._complete_official_stadiums(official) == {1}
    official[-1]["boats"].pop()
    assert bootstrap._complete_official_stadiums(official) == set()


def test_task_and_admin_status_persist_on_sqlite(monkeypatch, tmp_path):
    db_path = tmp_path / "bootstrap.db"
    monkeypatch.setattr(bootstrap, "db_connect", lambda: sqlite3.connect(db_path))
    bootstrap._ensure_tables()
    bootstrap._write_task(
        bootstrap.OPENAPI_TASK,
        TARGET,
        "failure",
        {"next_attempt_at": _now(0, 25).isoformat(timespec="seconds")},
    )
    stored = bootstrap._load_task(bootstrap.OPENAPI_TASK, TARGET)
    assert stored["status"] == "failure"
    assert stored["run_count"] == 1
    bootstrap._write_status(TARGET, "error", "unresolved", {"missing": [1]})
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, message FROM system_status WHERE check_name=? AND check_date=?",
            (bootstrap.STATUS_NAME, TARGET.isoformat()),
        ).fetchone()
    assert row == ("error", "unresolved")


def test_official_retry_writes_only_missing_stadiums(monkeypatch, tmp_path):
    races = []
    for stadium in (1, 2):
        for race_number in range(1, 13):
            races.append(
                {
                    "stadium_number": stadium,
                    "race_number": race_number,
                    "boats": [
                        {"racer_number": boat, "assigned_motor_number": boat}
                        for boat in range(1, 7)
                    ],
                }
            )
    source = tmp_path / "B260812.TXT"
    source.write_bytes(b"fixture")
    selected = []
    monkeypatch.setattr(bootstrap, "_manifest_stadiums", lambda _target: ([1, 2], "available"))
    monkeypatch.setattr(bootstrap.official_dl, "fetch_one", lambda *_args: source)
    monkeypatch.setattr(bootstrap, "parse_b_text", lambda *_args: races)

    class DummyConnection:
        def commit(self):
            return None

    @contextmanager
    def connection():
        yield DummyConnection()

    monkeypatch.setattr(bootstrap, "db_connect", connection)
    monkeypatch.setattr(
        bootstrap,
        "upsert_b",
        lambda _conn, payload: selected.extend(payload) or (len(payload), len(payload) * 6),
    )
    monkeypatch.setattr(bootstrap, "_record_phase_success", lambda *_args, **_kwargs: None)

    assert bootstrap.collect_official(
        TARGET,
        _now(0, 0),
        {"detail": {"missing_stadiums": [2]}},
    ) is True
    assert {race["stadium_number"] for race in selected} == {2}
