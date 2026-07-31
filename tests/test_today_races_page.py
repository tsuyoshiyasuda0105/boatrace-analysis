import os
from contextlib import contextmanager

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


@contextmanager
def _fake_connection():
    yield object()


def _member_client(monkeypatch):
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
                    "level": "g23_optb_tri",
                    "label": "G2/G3 1-2-3",
                    "bet": "3連単 1-2-3 100円",
                    "recovery": 190.3,
                    "hit_rate": 31.8,
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
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
    return client


def test_today_races_page_reads_precomputed_market_snapshot(monkeypatch):
    response = _member_client(monkeypatch).get(
        "/member/today-races?date=2026-07-30"
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "本日のレース" in html
    assert "採用確定" in html
    assert "G2/G3 1-2-3" in html
    assert "3連単 1-2-3 100円" in html


def test_races_page_includes_roi_list_and_market_snapshot(monkeypatch):
    response = _member_client(monkeypatch).get("/races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "本日のレース" in html
    assert "ROIが高いレース候補" in html
    assert "G2/G3 1-2-3" in html
    assert "marketSignalsLoaded = Boolean" in html


def test_today_navigation_keeps_all_daily_information_on_races_page(monkeypatch):
    response = _member_client(monkeypatch).get("/races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert 'href="/races?date=2026-07-30"' in html
    assert "本日のレース・会場情報・ROI候補一覧" in html
