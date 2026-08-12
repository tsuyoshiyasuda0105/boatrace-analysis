import json
import sqlite3

from scripts.check_post_run_integrity import check_motor_history_caches


def _conn_with_motor_payload(payload: dict):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
            race_id TEXT PRIMARY KEY,
            race_date TEXT,
            stadium_number INTEGER,
            race_number INTEGER
        );
        CREATE TABLE page_html_cache (
            cache_key TEXT PRIMARY KEY,
            html TEXT
        );
        """
    )
    race_id = "20260812-02-01"
    conn.execute("INSERT INTO races VALUES (?, ?, 2, 1)", (race_id, "2026-08-12"))
    for boat in range(1, 7):
        conn.execute(
            "INSERT INTO page_html_cache VALUES (?, ?)",
            (f"motor_history_v9:{race_id}:{boat}", json.dumps(payload)),
        )
    return conn, race_id


def _payload(history):
    return {
        "current": {"motor_number": 10},
        "history": history,
        "position_rows": [{"boat_number": boat} for boat in range(1, 7)],
    }


def test_empty_motor_history_is_warning_not_corruption():
    conn, race_id = _conn_with_motor_payload(_payload([]))

    status, _message, detail = check_motor_history_caches(
        conn, "2026-08-12", [race_id]
    )

    assert status == "warning"
    assert detail["invalid_motor_histories_count"] == 0
    assert detail["empty_motor_histories_count"] == 6
    assert detail["empty_history_by_stadium"] == {"2": 6}


def test_missing_motor_history_field_remains_error():
    payload = _payload([])
    del payload["history"]
    conn, race_id = _conn_with_motor_payload(payload)

    status, _message, detail = check_motor_history_caches(
        conn, "2026-08-12", [race_id]
    )

    assert status == "error"
    assert detail["invalid_motor_histories_count"] == 6
