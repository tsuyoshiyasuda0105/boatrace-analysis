import os
from contextlib import contextmanager

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


@contextmanager
def _fake_connection():
    yield object()


def test_today_races_page_does_not_hydrate_grid_badges(monkeypatch):
    race = {
        "race_id": "202607300101",
        "race_date": "2026-07-30",
        "race_number": 1,
        "race_closed_at": "2026-07-30 08:32:00",
        "stadium_number": 1,
        "stadium_name": "Kiryu",
        "results_count": 0,
    }
    payload = {
        "date": "2026-07-30",
        "signals": {
            race["race_id"]: {
                "n_female": 0,
                "l4": {
                    "level": "g23_optb_tri",
                    "label": "G2/G3 1-2-3",
                    "bet": "3連単 1-2-3",
                },
            }
        },
    }
    monkeypatch.setattr(web_app, "db_connect", _fake_connection)
    monkeypatch.setattr(web_app, "_races_for_date", lambda *_args, **_kwargs: [race])
    monkeypatch.setattr(web_app, "_read_json_cache", lambda *_args: payload)
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_hydrate_market_race_badges",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("today ROI list must not hydrate race-grid badges")
        ),
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True

    response = client.get("/member/today-races?date=2026-07-30")

    assert response.status_code == 200
    assert "G2/G3 1-2-3" in response.get_data(as_text=True)
