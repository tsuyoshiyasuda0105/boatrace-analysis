import json
from datetime import date

from scripts import check_program_source_gate as gate
from scripts import render_regular_scheduler as scheduler


TARGET = date(2026, 8, 12)


def _official_race():
    return {
        "race_id": "20260812-01-01",
        "race_date": "2026-08-12",
        "stadium_number": 1,
        "race_number": 1,
        "race_closed_at": "2026-08-12 10:30:00",
        "boats": [
            {
                "boat_number": boat,
                "racer_number": 1000 + boat,
                "assigned_motor_number": 10 + boat,
            }
            for boat in range(1, 7)
        ],
    }


def _openapi_race():
    return {
        "race_id": "20260812-01-01",
        "race_date": "2026-08-12",
        "race_stadium_number": 1,
        "race_number": 1,
        "race_closed_at": "2026-08-12T10:30:00",
        "boats": [
            {
                "racer_boat_number": boat,
                "racer_number": 1000 + boat,
                "racer_assigned_motor_number": 10 + boat,
            }
            for boat in range(1, 7)
        ],
    }


def _configure_paths(monkeypatch, tmp_path):
    programs = tmp_path / "programs"
    openapi = tmp_path / "openapi"
    raw = tmp_path / "raw"
    programs.mkdir()
    openapi.mkdir()
    monkeypatch.setattr(gate.config, "OFFICIAL_PROGRAMS_DIR", programs)
    monkeypatch.setattr(gate.config, "OPENAPI_RAW_DIR", openapi)
    monkeypatch.setattr(gate.config, "RAW_DIR", raw)
    (programs / "B260812.TXT").write_bytes(b"official")
    monkeypatch.setattr(gate, "parse_b_text", lambda *_args: [_official_race()])
    monkeypatch.setattr(
        gate,
        "_db_program_counts",
        lambda _date: {"races": 1, "entries": 6, "detail_entries": 6},
    )
    return openapi, raw


def test_gate_is_ready_with_three_complete_sources(monkeypatch, tmp_path):
    openapi, _raw = _configure_paths(monkeypatch, tmp_path)
    (openapi / "2026-08-12_programs.json").write_text(
        json.dumps({"programs": [_openapi_race()]}), encoding="utf-8"
    )
    monkeypatch.setattr(
        gate,
        "fetch_official_race_manifest",
        lambda _date: {
            "status": "available",
            "target_date": "2026-08-12",
            "expected_payload": {
                "stadiums": [{"stadium_number": 1, "race_numbers": [1]}]
            },
        },
    )

    result = gate.check_program_source_gate(TARGET)

    assert result["gate_status"] == "ready"
    assert result["official_races"] == 1
    assert result["openapi_races"] == 1
    assert result["expected_races"] == 1


def test_gate_waits_when_openapi_raw_is_not_available(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(gate.openapi, "fetch_programs", lambda _date: None)
    monkeypatch.setattr(
        gate,
        "fetch_official_race_manifest",
        lambda _date: {
            "status": "available",
            "target_date": "2026-08-12",
            "expected_payload": {
                "stadiums": [{"stadium_number": 1, "race_numbers": [1]}]
            },
        },
    )

    result = gate.check_program_source_gate(TARGET)

    assert result["gate_status"] == "retry_wait"
    assert result["openapi_state"] == "unavailable"


def test_gate_recovers_missing_ephemeral_raw_files(monkeypatch, tmp_path):
    openapi, _raw = _configure_paths(monkeypatch, tmp_path)
    official_path = gate.config.OFFICIAL_PROGRAMS_DIR / "B260812.TXT"
    official_path.unlink()
    calls = []

    def fetch_official(_kind, _date):
        calls.append("official")
        official_path.write_bytes(b"official")
        return official_path

    monkeypatch.setattr(gate.official_dl, "fetch_one", fetch_official)
    monkeypatch.setattr(
        gate.openapi,
        "fetch_programs",
        lambda _date: calls.append("openapi") or {"programs": [_openapi_race()]},
    )
    monkeypatch.setattr(
        gate,
        "fetch_official_race_manifest",
        lambda _date: {
            "status": "available",
            "target_date": "2026-08-12",
            "expected_payload": {
                "stadiums": [{"stadium_number": 1, "race_numbers": [1]}]
            },
        },
    )

    result = gate.check_program_source_gate(TARGET)

    assert result["gate_status"] == "ready"
    assert calls == ["official", "openapi"]
    assert not (openapi / "2026-08-12_programs.json").exists()


def test_gate_blocks_malformed_openapi_raw(monkeypatch, tmp_path):
    openapi, _raw = _configure_paths(monkeypatch, tmp_path)
    (openapi / "2026-08-12_programs.json").write_text("{", encoding="utf-8")

    result = gate.check_program_source_gate(TARGET)

    assert result["gate_status"] == "blocked"
    assert result["reason"] == "openapi_JSONDecodeError"


def test_gate_reuses_available_manifest_cache(monkeypatch, tmp_path):
    openapi, raw = _configure_paths(monkeypatch, tmp_path)
    (openapi / "2026-08-12_programs.json").write_text(
        json.dumps({"programs": [_openapi_race()]}), encoding="utf-8"
    )
    manifest_dir = raw / "official_manifest"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "2026-08-12.json").write_text(
        json.dumps(
            {
                "status": "available",
                "target_date": "2026-08-12",
                "expected_payload": {
                    "stadiums": [{"stadium_number": 1, "race_numbers": [1]}]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate,
        "fetch_official_race_manifest",
        lambda _date: (_ for _ in ()).throw(AssertionError("must use cache")),
    )

    assert gate.check_program_source_gate(TARGET)["gate_status"] == "ready"


def test_gate_blocks_malformed_manifest_cache(monkeypatch, tmp_path):
    openapi, raw = _configure_paths(monkeypatch, tmp_path)
    (openapi / "2026-08-12_programs.json").write_text(
        json.dumps({"programs": [_openapi_race()]}), encoding="utf-8"
    )
    manifest_dir = raw / "official_manifest"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "2026-08-12.json").write_text("{", encoding="utf-8")

    result = gate.check_program_source_gate(TARGET)

    assert result["gate_status"] == "blocked"
    assert result["manifest_state"] == "parse_error"


def test_gate_blocks_when_complete_raw_sources_were_not_fully_persisted(monkeypatch, tmp_path):
    openapi, _raw = _configure_paths(monkeypatch, tmp_path)
    (openapi / "2026-08-12_programs.json").write_text(
        json.dumps({"programs": [_openapi_race()]}), encoding="utf-8"
    )
    monkeypatch.setattr(
        gate,
        "fetch_official_race_manifest",
        lambda _date: {
            "status": "available",
            "target_date": "2026-08-12",
            "expected_payload": {
                "stadiums": [{"stadium_number": 1, "race_numbers": [1]}]
            },
        },
    )
    monkeypatch.setattr(
        gate,
        "_db_program_counts",
        lambda _date: {"races": 1, "entries": 5, "detail_entries": 5},
    )

    result = gate.check_program_source_gate(TARGET)

    assert result["gate_status"] == "blocked"
    assert result["reason"] == "db_program_incomplete"


def test_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(
        gate,
        "check_program_source_gate",
        lambda _date: {"gate_status": "retry_wait"},
    )

    assert gate.main(["--date", "2026-08-12"]) == 3
    assert json.loads(capsys.readouterr().out)["gate_status"] == "retry_wait"


def test_scheduler_gate_reuses_daily_success(monkeypatch):
    monkeypatch.setattr(scheduler, "task_success_exists", lambda *_args: True)
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must reuse daily success")
        ),
    )

    assert scheduler.run_program_source_gate("2026-08-12") is True


def test_scheduler_gate_records_failure(monkeypatch):
    records = []
    monkeypatch.setattr(scheduler, "task_success_exists", lambda *_args: False)
    monkeypatch.setattr(scheduler, "run_py", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda task, run_date, status, detail=None: records.append(
            (task, run_date, status, detail)
        ),
    )

    assert scheduler.run_program_source_gate("2026-08-12") is False
    assert records == [
        ("render_program_source_gate_v1", "2026-08-12", "failure", None)
    ]


def test_morning_stops_before_downstream_generation_when_gate_is_not_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: calls.append((tuple(args), timeout)) or True,
    )
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda _date: False)
    monkeypatch.setattr(
        scheduler,
        "run_tides",
        lambda _now: (_ for _ in ()).throw(AssertionError("must not run tides")),
    )

    now = scheduler.datetime(2026, 8, 12, 6, 0, tzinfo=scheduler.JST)

    assert scheduler.run_morning(now) is False
    assert len(calls) == 2


def test_signal_refresh_stops_before_roi_generation_when_gate_is_not_ready(monkeypatch):
    records = []
    monkeypatch.setattr(scheduler, "task_attempt_exists", lambda *_args: False)
    monkeypatch.setattr(scheduler, "signal_refresh_recently_running", lambda _now: False)
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda _date: False)
    monkeypatch.setattr(
        scheduler,
        "run_derived_start_stats",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not build ROI inputs")),
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda task, run_date, status, detail=None: records.append(
            (task, run_date, status, detail)
        ),
    )

    now = scheduler.datetime(2026, 8, 12, 10, 5, tzinfo=scheduler.JST)

    assert scheduler.run_signal_refresh_slot(now) is False
    assert records[-1][2:] == ("failure", "program_source_gate_not_ready")


def test_nightly_stops_before_tomorrow_predictions_when_gate_is_not_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: calls.append((tuple(args), timeout)) or True,
    )
    monkeypatch.setattr(scheduler, "run_tides", lambda _now: True)
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda _date: False)

    now = scheduler.datetime(2026, 8, 12, 23, 30, tzinfo=scheduler.JST)

    assert scheduler.run_nightly(now) is False
    assert not any(
        args[:2] == ("scripts/render_cache_predictions.py", "--date")
        and "2026-08-13" in args
        for args, _timeout in calls
    )
