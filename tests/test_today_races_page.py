import os
from contextlib import contextmanager
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


@contextmanager
def _fake_connection():
    yield object()


def test_today_pages_default_to_jst_date(monkeypatch):
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-04")
    monkeypatch.setattr(web_app, "db_connect", _fake_connection)
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda target_date, conn=None: [],
    )
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date",
        lambda _target_date, conn=None: {},
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    races_response = client.get("/")
    today_response = client.get("/member/today-races")

    assert races_response.status_code == 302
    assert races_response.headers["Location"].endswith("/races?date=2026-08-04")
    assert today_response.status_code == 302
    assert today_response.headers["Location"].endswith("/login?next=/member/today-races")
 

def test_public_top_routes_render_without_login(monkeypatch):
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-04")
    monkeypatch.setattr(web_app, "db_connect", _fake_connection)
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda target_date, conn=None: [],
    )
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date",
        lambda _target_date, conn=None: {},
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()

    races_response = client.get("/races?date=2026-08-04")
    assert races_response.status_code == 200
    assert "2026-08-04" in races_response.get_data(as_text=True)


def test_public_routes_do_not_require_login():
    source = (Path(__file__).resolve().parents[1] / "src" / "web" / "app.py").read_text(
        encoding="utf-8"
    )

    index_block = source.split('@app.route("/")', 1)[1].split('@app.route("/races")', 1)[0]
    races_block = source.split('@app.route("/races")', 1)[1].split('@app.route("/member/today-races")', 1)[0]
    detail_block = source.split('@app.route("/race/<race_id>")', 1)[1].split('@app.route("/api/race/<race_id>/value-bets")', 1)[0]

    assert "@login_required" not in index_block
    assert "@login_required" not in races_block
    assert "@login_required" not in detail_block


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
        session["role"] = "admin"
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
        session["role"] = "admin"

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
    assert "mode: 'top-lightweight'" in html
    assert "renderTodaysPicks = function()" not in html
    assert "async function loadMarketSignals()" not in html
    assert "renderTodaysPicks: { calls: 0, result: 'not_loaded_top_only' }" in html


def test_races_page_disables_browser_cache_for_member_html(monkeypatch):
    response = _member_client(monkeypatch).get("/races?date=2026-07-30")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, private"


def test_top_page_template_uses_slower_refresh_and_hides_tile_countdown():
    source = Path("src/web/templates/index.html").read_text(encoding="utf-8")

    assert "{% if not show_today_picks_panel|default(false) %}" in source
    assert "window.setInterval(updateRaceState, 60000);" in source
    assert "marketSignalsRequests: 0" in source
    assert "const showRaceTileCountdown = roiPicksVisible;" in source
    assert "if (tilEl && showRaceTileCountdown && minutesUntil <= 60)" in source
    assert "if (!roiPicksVisible) return;" in source
    assert "setInterval(refreshDashboard, 60000);" in source
    assert "let raceBadgesCache =" in source
    assert "raceBadgesCache = raceBadges;" in source


def test_top_page_template_shows_visible_vs_reference_counts():
    source = Path("src/web/templates/index.html").read_text(encoding="utf-8")

    assert 'id="todays-picks-count-note"' in source
    assert "表示 ${visibleCount}件 / 元候補 ${totalSignalCount}件" in source
    assert "参考非表示 ${hiddenReferenceCount}件" in source

def test_market_signals_api_uses_short_server_cache():
    source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert '@cached(ttl=8, past_ttl=3600)' in source


def test_render_web_worker_restarts_are_not_too_frequent():
    source = Path("render.yaml").read_text(encoding="utf-8")

    assert "--graceful-timeout 30" in source
    assert "--max-requests 1000" in source
    assert "--max-requests 200 " not in source


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
    assert "signals" not in (written["payload"]["initial_market_signals"] or {})


def test_write_top_page_snapshot_preserves_existing_daily_badges(monkeypatch):
    written = {}
    existing_snapshot = {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": "2026-07-30",
        "stadium_groups": [],
        "initial_market_signals": {
            "date": "2026-07-30",
            "race_badges": {
                "202607300101": {
                    "accident": {"label": "事故率0.50+ 1号艇"},
                    "ace_motor": {"label": "エースモーター 1号艇"},
                    "entry_change": {"label": "騎乗注意"},
                }
            },
            "accident_watch": {
                "202607300101": {"label": "事故率0.70+ 1号艇"}
            },
        },
        "empty": False,
    }

    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: existing_snapshot)
    monkeypatch.setattr(
        web_app,
        "_write_json_cache",
        lambda key, payload: written.update({"key": key, "payload": payload}),
    )

    payload = {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": "2026-07-30",
        "stadium_groups": [{"stadium_number": 1, "stadium_name": "桐生", "races": []}],
        "initial_market_signals": {
            "date": "2026-07-30",
            "race_badges": {},
            "accident_watch": {},
        },
        "empty": False,
        "source": "web-lightweight-fallback",
    }

    web_app._write_top_page_snapshot("2026-07-30", payload)

    saved = written["payload"]["initial_market_signals"]
    assert saved["race_badges"]["202607300101"]["accident"]["label"] == "事故率0.50+ 1号艇"
    assert saved["race_badges"]["202607300101"]["ace_motor"]["label"] == "エースモーター 1号艇"
    assert saved["race_badges"]["202607300101"]["entry_change"]["label"] == "騎乗注意"
    assert saved["accident_watch"]["202607300101"]["label"] == "事故率0.70+ 1号艇"


def test_build_top_page_snapshot_payload_can_skip_market_signals(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda *_args, **_kwargs: [
            {
                "race_id": "202607300101",
                "stadium_number": 1,
                "stadium_name": "Kiryu",
            }
        ],
    )
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date",
        lambda *_args, **_kwargs: {1: {"weather": "sunny"}},
    )
    monkeypatch.setattr(
        web_app,
        "_race_grid_badges_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("market signals should be skipped")
        ),
    )

    payload = web_app._build_top_page_snapshot_payload(
        "2026-07-30",
        conn=object(),
        include_market_signals=False,
    )

    assert payload["stadium_groups"][0]["environment"]["weather"] == "sunny"
    assert payload["initial_market_signals"]["signals"] == {}
    assert payload["initial_market_signals"]["race_badges"] == {}


def test_races_page_does_not_self_heal_from_web_request_by_default(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("BOATRACE_WEB_SELF_HEAL", raising=False)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-07-30")
    monkeypatch.setattr(web_app, "db_connect", _fake_connection)
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda _target_date, conn=None: [],
    )
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date",
        lambda _target_date, conn=None: {},
    )
    monkeypatch.setattr(
        web_app.openapi,
        "collect_all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("web request must not run external data collection")
        ),
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"

    response = client.get("/races?date=2026-07-30")

    assert response.status_code == 200


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
            "race_badges": {
                "202607300101": {"accident": {"label": "事故率0.50+ 1号艇"}}
            },
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
        session["role"] = "admin"

    response = client.get("/races?date=2026-07-30")

    assert response.status_code == 200
    assert "Kiryu" in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, private"


def test_races_page_repairs_empty_top_snapshot_badges(monkeypatch):
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
    written = {}
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(
        web_app,
        "_race_grid_badges_payload",
        lambda *_args, **_kwargs: {
            "date": "2026-07-30",
            "signals": {},
            "race_badges": {
                "202607300101": {"accident": {"label": "事故率0.50+ 1号艇"}}
            },
            "accident_watch": {},
        },
    )
    monkeypatch.setattr(
        web_app,
        "_write_top_page_snapshot",
        lambda target_date, payload=None: written.update({"target_date": target_date, "payload": payload}) or payload,
    )
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("TOP snapshot repair must not read races")
        ),
    )
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"

    response = client.get("/races?date=2026-07-30")

    assert response.status_code == 200
    assert written["target_date"] == "2026-07-30"
    saved = written["payload"]["initial_market_signals"]["race_badges"]
    assert saved["202607300101"]["accident"]["label"] == "事故率0.50+ 1号艇"


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


def test_race_grid_badges_payload_includes_cached_signals(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_read_json_cache_stale",
        lambda *_args: {
            "date": "2026-07-30",
            "signals": {
                "202607300101": {"race_id": "202607300101", "l4": {"level": "general"}},
                "202607300102": {"race_id": "202607300102", "l4": {"level": "general"}},
            },
            "race_badges": {},
        },
    )
    monkeypatch.setattr(web_app, "_hydrate_market_race_badges", lambda payload, _date: payload)

    payload = web_app._race_grid_badges_payload("2026-07-30", ["202607300101"])

    assert set(payload["signals"]) == {"202607300101"}
    assert payload["signals"]["202607300101"]["l4"]["level"] == "general"


def test_today_navigation_opens_dedicated_candidate_page(monkeypatch):
    response = _member_client(monkeypatch).get("/races?date=2026-07-30")
    html = response.get_data(as_text=True)

    assert 'href="/member/today-races?date=2026-07-30"' in html
    assert 'title="本日のROI候補一覧"' in html
