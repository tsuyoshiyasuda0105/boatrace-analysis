import os
from contextlib import contextmanager

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


@contextmanager
def _fake_connection():
    yield object()


def _member_client(monkeypatch, races=None):
    monkeypatch.setattr(web_app, "db_connect", _fake_connection)
    monkeypatch.setattr(web_app, "_races_for_date", lambda *_args, **_kwargs: list(races or []))
    monkeypatch.setattr(
        web_app,
        "load_roi_history_races",
        lambda *_args, **_kwargs: [],
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
    return client


def test_today_races_page_links_to_past_history(monkeypatch):
    race = {
        "race_id": "202607300101",
        "race_date": "2026-07-30",
        "race_number": 1,
        "race_closed_at": "2026-07-30 08:32:00",
        "stadium_number": 1,
        "stadium_name": "Kiryu",
        "results_count": 0,
    }
    monkeypatch.setattr(web_app, "_read_json_cache", lambda *_args: {"date": "2026-07-30", "signals": {}})
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: None)
    monkeypatch.setattr(web_app, "_hydrate_market_race_badges", lambda payload, _date: payload)

    response = _member_client(monkeypatch, [race]).get(
        "/member/today-races?date=2026-07-30"
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/member/today-races/history"' in html
    assert "過去履歴" in html


def test_today_race_history_page_clamps_future_to_past(monkeypatch):
    response = _member_client(monkeypatch).get(
        "/member/today-races/history?from=2026-07-01&to=2099-01-01"
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "過去履歴" in html
    assert 'action="/member/today-races/history"' in html
    assert "2099-01-01" not in html
