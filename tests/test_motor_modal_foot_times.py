from contextlib import contextmanager
from pathlib import Path

import pytest

from src.web import app as web_app


RACE_DETAIL_JS = Path("src/web/static/race_detail.js")


class _CurrentRowConnection:
    def __init__(self, row):
        self.row = row

    def execute(self, _sql, _params):
        return self

    def fetchone(self):
        return self.row


def _payload_for_times(monkeypatch, *, lap_time, turn_time, straight_time):
    row = (
        None,
        41.2,
        59.8,
        "固定選手",
        4321,
        3,
        6.71,
        -0.03,
        lap_time,
        turn_time,
        straight_time,
        1,
        None,
    )

    @contextmanager
    def fake_db_connect():
        yield _CurrentRowConnection(row)

    monkeypatch.setattr(web_app, "db_connect", fake_db_connect)
    monkeypatch.setattr(web_app, "_current_race_position_rows", lambda _race_id: [])
    return web_app._motor_history_payload(
        "202608160101",
        1,
        info={
            "race_date": "2026-08-16",
            "race_number": 1,
            "stadium_number": 1,
            "stadium_name": "桐生",
        },
    )


def test_motor_history_current_contains_all_exhibition_foot_times(monkeypatch):
    payload = _payload_for_times(
        monkeypatch,
        lap_time=37.42,
        turn_time=5.31,
        straight_time=7.18,
    )

    assert payload is not None
    assert payload["current"]["lap_time"] == pytest.approx(37.42)
    assert payload["current"]["turn_time"] == pytest.approx(5.31)
    assert payload["current"]["straight_time"] == pytest.approx(7.18)
    assert payload["current"]["dash_time"] == payload["current"]["lap_time"]


def test_motor_history_current_preserves_unprovided_kiryu_times(monkeypatch):
    payload = _payload_for_times(
        monkeypatch,
        lap_time=None,
        turn_time=7.57,
        straight_time=None,
    )

    assert payload is not None
    assert payload["current"]["turn_time"] == pytest.approx(7.57)
    assert payload["current"]["straight_time"] is None
    assert payload["current"]["lap_time"] is None


def test_motor_modal_renders_current_foot_times_and_missing_marker():
    script = RACE_DETAIL_JS.read_text(encoding="utf-8")

    assert 'aria-label="本日の展示足"' in script
    assert "footTime(current.turn_time)" in script
    assert "footTime(current.straight_time)" in script
    assert "footTime(current.lap_time)" in script
    assert 'value == null ? "—"' in script
