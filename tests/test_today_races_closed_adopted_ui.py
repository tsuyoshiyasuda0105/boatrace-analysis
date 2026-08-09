import os
from contextlib import contextmanager

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


@contextmanager
def _fake_connection():
    yield object()


def test_today_races_page_marks_closed_adopted_rows_before_result_history(monkeypatch):
    race = {
        "race_id": "202607300101",
        "race_date": "2026-07-30",
        "race_number": 1,
        "race_closed_at": "2026-07-30 08:32:00",
        "stadium_number": 1,
        "stadium_name": "桐生",
        "results_count": 0,
    }
    payload = {
        "date": "2026-07-30",
        "signals": {
            race["race_id"]: {
                "n_female": 0,
                "l4": {
                    "level": "omura_14_exa",
                    "label": "大村 2連単1-4",
                    "bet": "2連単 1-4",
                    "recovery": 152.8,
                    "hit_rate": 31.2,
                    "is_exacta_niche": True,
                    "is_display_confirmed": True,
                },
            }
        },
    }

    monkeypatch.setattr(web_app, "db_connect", _fake_connection)
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda _target_date, conn=None: [race],
    )
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date",
        lambda _target_date, conn=None: {},
    )
    monkeypatch.setattr(web_app, "_read_json_cache", lambda *_args: payload)
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_hydrate_market_race_badges",
        lambda cached, _target_date: cached,
    )
    monkeypatch.setattr(
        web_app,
        "load_roi_history_races_by_race_ids",
        lambda *_args, **_kwargs: [],
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"

    response = client.get("/member/today-races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "is-adopted-closed" in html
    assert "採用済" in html
