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
    assert "todays-pick-row is-closed" in html
    assert "3連単 1-2-3 100円" in html


def test_today_races_page_marks_adopted_closed_rows_with_profit(monkeypatch):
    race = {
        "race_id": "202607300101",
        "race_date": "2026-07-30",
        "race_number": 1,
        "race_closed_at": "2026-07-30 08:32:00",
        "stadium_number": 1,
        "stadium_name": "桐生",
        "results_count": 6,
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
        lambda *_args, **_kwargs: [
            {
                "race_id": race["race_id"],
                "strategy_label": "G2/G3 1-2-3",
                "bet": "3連単 1-2-3 100円",
                "stake": 100,
                "payout": 560,
                "profit": 460,
                "recovery": 560.0,
                "is_hit": True,
            }
        ],
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True

    response = client.get("/member/today-races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "is-history-hit is-adopted-row" in html
    assert "HIT" in html
    assert "+460円" in html


def test_races_page_excludes_roi_list_and_does_not_read_snapshot(monkeypatch):
    client = _member_client(monkeypatch)
    monkeypatch.setattr(
        web_app,
        "_read_json_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("TOP must not load ROI candidate data")
        ),
    )
    response = client.get("/races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "2026-07-30" in html
    assert "ROIが高いレース候補" not in html
    assert "G2/G3 1-2-3" not in html
    assert "const marketSignalsEnabled = false" in html


def test_races_page_writes_lightweight_top_snapshot_on_cache_miss(monkeypatch):
    written = {}

    def fake_write(target_date, payload=None):
        written["target_date"] = target_date
        written["payload"] = payload
        return payload

    client = _member_client(monkeypatch)
    monkeypatch.setattr(web_app, "_write_top_page_snapshot", fake_write)

    response = client.get("/races?date=2026-07-30")

    assert response.status_code == 200
    assert written["target_date"] == "2026-07-30"
    assert written["payload"]["source"] == "web-lightweight-fallback"
    assert written["payload"]["stadium_groups"]


def test_races_page_uses_top_snapshot_without_db_or_badge_hydration(monkeypatch):
    race = {
        "race_id": "202607300101",
        "race_date": "2026-07-30",
        "race_number": 1,
        "race_closed_at": "2026-07-30 08:32:00",
        "stadium_number": 1,
        "stadium_name": "Kiryu",
        "results_count": 0,
    }
    snapshot = {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": "2026-07-30",
        "stadium_groups": [
            {
                "stadium_number": 1,
                "stadium_name": "Kiryu",
                "environment": {},
                "races": [race],
            }
        ],
        "initial_market_signals": {
            "date": "2026-07-30",
            "race_badges": {},
            "accident_watch": {},
        },
        "empty": False,
    }
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: snapshot)
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("TOP snapshot path must not read races")
        ),
    )
    monkeypatch.setattr(
        web_app,
        "_hydrate_market_race_badges",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("TOP snapshot path must not hydrate badges")
        ),
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True

    response = client.get("/races?date=2026-07-30")

    assert response.status_code == 200
    assert "Kiryu" in response.get_data(as_text=True)
    assert "stale-while-revalidate=300" in response.headers["Cache-Control"]


def test_race_grid_badges_fallback_builds_tags_without_market_cache(monkeypatch):
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: None)

    def fake_hydrate(payload, target_date):
        assert target_date == "2026-07-30"
        return {
            **payload,
            "race_badges": {
                "202607300101": {
                    "accident": {
                        "items": [{"boat": 2, "rate": 0.61, "class_label": "A1"}],
                        "max_rate": 0.61,
                    },
                    "escape": {
                        "items": [{"boat": 1, "rate": 72.5}],
                        "max_rate": 72.5,
                    },
                },
                "202607300102": {
                    "escape": {
                        "items": [{"boat": 1, "rate": 80.0}],
                        "max_rate": 80.0,
                    },
                },
            },
        }

    monkeypatch.setattr(web_app, "_hydrate_market_race_badges", fake_hydrate)

    payload = web_app._race_grid_badges_payload(
        "2026-07-30",
        ["202607300101"],
    )

    assert set(payload["race_badges"]) == {"202607300101"}
    badge = payload["race_badges"]["202607300101"]
    assert badge["accident"]["label"] == "事故率0.50+ 2号:A1 0.61"
    assert badge["escape"]["label"] == "1号:逃げ 72.5%"


def test_today_navigation_opens_dedicated_candidate_page(monkeypatch):
    response = _member_client(monkeypatch).get("/races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert 'href="/member/today-races?date=2026-07-30"' in html
    assert 'title="本日のROI候補一覧"' in html
