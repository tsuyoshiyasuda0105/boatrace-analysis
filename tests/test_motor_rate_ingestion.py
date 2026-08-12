import sqlite3
from pathlib import Path

from scripts.backfill_official import upsert_b
from scripts.check_post_run_integrity import check_race_detail_rows
from src.collectors.openapi import upsert_programs
from src.web import app as web_app


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(Path("src/db/schema.sql").read_text(encoding="utf-8"))
    return conn


def _official(rate=42.5):
    return [{
        "race_id": "20260812-07-12", "race_date": "2026-08-12",
        "stadium_number": 7, "race_number": 12,
        "boats": [{
            "boat_number": 1, "racer_number": 3946,
            "assigned_motor_number": 60,
            "assigned_motor_top_2_percent": rate,
        }],
    }]


def _openapi(rate):
    return {"programs": [{
        "race_date": "2026-08-12", "race_stadium_number": 7,
        "race_number": 12, "race_closed_at": "2026-08-12T20:36:00",
        "boats": [{
            "racer_boat_number": 1, "racer_number": 3946,
            "racer_assigned_motor_number": 60,
            "racer_assigned_motor_top_2_percent": rate,
        }],
    }]}


def _rate(conn):
    return conn.execute(
        "SELECT assigned_motor_top_2_percent FROM race_entries "
        "WHERE race_id=? AND boat_number=1", ("20260812-07-12",)
    ).fetchone()[0]


def test_openapi_zero_does_not_overwrite_positive_official_motor_rate():
    conn = _conn()
    upsert_b(conn, _official())

    upsert_programs(conn, _openapi(0.0))

    assert _rate(conn) == 42.5


def test_openapi_zero_does_not_reuse_old_rate_when_motor_changes():
    conn = _conn()
    upsert_b(conn, _official())
    payload = _openapi(0.0)
    payload["programs"][0]["boats"][0]["racer_assigned_motor_number"] = 61

    upsert_programs(conn, payload)

    motor_number, rate = conn.execute(
        "SELECT assigned_motor_number, assigned_motor_top_2_percent "
        "FROM race_entries WHERE race_id=? AND boat_number=1",
        ("20260812-07-12",),
    ).fetchone()
    assert (motor_number, rate) == (61, 0.0)


def test_official_backfill_repairs_matching_motor_zero_rate():
    conn = _conn()
    upsert_programs(conn, _openapi(0.0))

    upsert_b(conn, _official())

    assert _rate(conn) == 42.5


def test_official_backfill_does_not_apply_rate_to_different_motor():
    conn = _conn()
    upsert_programs(conn, _openapi(0.0))
    official = _official()
    official[0]["boats"][0]["assigned_motor_number"] = 61

    upsert_b(conn, official)

    assert _rate(conn) == 0.0


def test_post_run_integrity_rejects_six_all_zero_motor_rates():
    conn = _conn()
    race_id = "20260812-07-12"
    conn.execute(
        "INSERT INTO races (race_id, race_date, stadium_number, race_number) "
        "VALUES (?, '2026-08-12', 7, 12)",
        (race_id,),
    )
    for boat in range(1, 7):
        conn.execute(
            "INSERT INTO race_entries "
            "(race_id, boat_number, racer_number, assigned_motor_number, "
            "assigned_motor_top_2_percent) VALUES (?, ?, ?, ?, 0)",
            (race_id, boat, 3900 + boat, 10 + boat),
        )

    status, _message, detail = check_race_detail_rows(
        conn, "2026-08-12", [race_id]
    )

    assert status == "warning"
    assert detail["missing"] == []
    assert detail["all_zero_motor_rates"] == [race_id]


def test_race_detail_recovers_all_zero_rates_from_current_cycle_history(monkeypatch):
    conn = _conn()
    conn.execute(
        "INSERT INTO races (race_id, race_date, stadium_number, race_number) "
        "VALUES ('20260811-07-01', '2026-08-11', 7, 1)"
    )
    for boat in range(1, 7):
        conn.execute(
            "INSERT INTO race_entries "
            "(race_id, boat_number, racer_number, assigned_motor_number) "
            "VALUES ('20260811-07-01', ?, ?, ?)",
            (boat, 3900 + boat, 10 + boat),
        )
        conn.execute(
            "INSERT INTO race_results (race_id, boat_number, finishing_position) "
            "VALUES ('20260811-07-01', ?, ?)",
            (boat, boat),
        )
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)
    monkeypatch.setattr(web_app, "_motor_cycle_start", lambda *_args: "2026-06-01")
    preds = [
        {
            "boat_number": boat,
            "assigned_motor_number": 10 + boat,
            "assigned_motor_top_2_percent": 0.0,
        }
        for boat in range(1, 7)
    ]

    web_app._recover_all_zero_motor_rates(
        preds,
        {"race_date": "2026-08-12", "stadium_number": 7},
    )

    assert [p["assigned_motor_top_2_percent"] for p in preds] == [100.0, 100.0, 0.0, 0.0, 0.0, 0.0]
    assert all(p["motor_rate_estimated"] for p in preds)
