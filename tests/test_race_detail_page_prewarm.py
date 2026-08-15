from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from scripts import prewarm_race_detail_pages as page_prewarm
from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "web" / "app.py"
SCRIPT_SOURCE = ROOT / "scripts" / "prewarm_race_detail_pages.py"


def test_race_detail_uses_fresh_page_cache_for_today_and_stale_for_past():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def race_detail(race_id: str):")
    end = source.index("@app.route", start)
    route_source = source[start:end]

    info_read = route_source.index("_race_basic_info(race_id)")
    fresh_cache_read = route_source.index("_read_page_html_cache(page_cache_key, 180)")
    stale_cache_read = route_source.index("_read_page_html_cache_stale(page_cache_key)")
    assert fresh_cache_read < info_read
    assert stale_cache_read < info_read
    assert "use_fresh_page_cache = race_date >= _today_jst_iso()" in route_source
    assert "_write_page_html_cache(page_cache_key, html)" in route_source


def test_manual_prewarm_uses_guarded_recompute_and_member_session():
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")

    assert 'BOATRACE_TASK_TRIGGER", "render-detail-prewarm"' in source
    assert 'sess["is_member"] = True' in source
    assert 'client.get(f"/race/{rid}?recompute=1")' in source
    assert "elapsed_seconds" in source


def test_manual_prewarm_verifies_every_persistent_page_and_can_repair_only_missing():
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")

    assert "def _missing_persistent_page_ids" in source
    assert "persistent_cache_missing" in source
    assert 'parser.add_argument("--missing-only", action="store_true")' in source
    assert 'parser.add_argument("--retry-missing", type=int, default=1)' in source
    assert 'summary["requested_races"] > 0' in source


def test_prewarm_fails_when_http_200_page_is_not_persisted(monkeypatch):
    class Session:
        def __enter__(self):
            return {}

        def __exit__(self, *_args):
            return False

    class Response:
        status_code = 200
        data = b"ok"

    class Client:
        def session_transaction(self):
            return Session()

        def get(self, _url):
            return Response()

    class App:
        testing = False

        def test_client(self):
            return Client()

    checks = iter([["race-2"], ["race-2"]])
    monkeypatch.setattr(page_prewarm, "_require_postgres", lambda: None)
    monkeypatch.setattr(page_prewarm, "_race_ids", lambda *_args: ["race-1", "race-2"])
    monkeypatch.setattr(page_prewarm, "_missing_persistent_page_ids", lambda _ids: next(checks))
    monkeypatch.setattr(page_prewarm.web_app, "create_app", lambda **_kwargs: App())

    summary = page_prewarm.prewarm("2026-08-11")

    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["persistent_missing"] == 1
    assert summary["failures"] == [
        {"race_id": "race-2", "status": "persistent_cache_missing"}
    ]


def test_race_detail_venue_environment_uses_date_cache_wrapper():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "@lru_cache(maxsize=64)\ndef _venue_environment_summaries_for_date_cached" in source
    assert "return _venue_environment_summaries_for_date_cached(target_date)" in source


def test_race_detail_venue_environment_cache_reuses_same_date(monkeypatch):
    calls: list[str] = []

    def fake_impl(target_date: str, conn=None):
        calls.append(target_date)
        return {1: {"label": target_date}}

    web_app._venue_environment_summaries_for_date_cached.cache_clear()
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date_impl",
        fake_impl,
    )

    first = web_app._venue_environment_summaries_for_date("2026-08-04")
    second = web_app._venue_environment_summaries_for_date("2026-08-04")

    assert first == second == {1: {"label": "2026-08-04"}}
    assert calls == ["2026-08-04"]


def test_parse_local_datetime_normalizes_naive_and_aware_values_to_jst():
    naive = web_app._parse_local_datetime("2026-08-08 16:30:00")
    aware = web_app._parse_local_datetime("2026-08-08T16:30:00+09:00")
    now = web_app._now_jst()

    assert naive is not None
    assert aware is not None
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    assert naive.utcoffset() == aware.utcoffset()
    assert (aware - now).total_seconds()
    assert (naive - now).total_seconds()


def test_venue_environment_accepts_timezone_aware_race_closed_at(monkeypatch):
    class FakeCursor:
        def fetchall(self):
            return [
                (
                    "202608080201",
                    2,
                    1,
                    "2026-08-08T16:30:00+09:00",
                    "fresh",
                    1,
                    2.0,
                    4,
                    1.0,
                    "mid",
                    120.0,
                    5.0,
                    30.0,
                    0,
                    0,
                    "2026-08-08T12:00:00+09:00",
                )
            ]

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeCursor()

    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-08")
    monkeypatch.setattr(
        web_app,
        "_now_jst",
        lambda: datetime(2026, 8, 8, 16, 0, tzinfo=web_app.JST),
    )

    result = web_app._venue_environment_summaries_for_date_impl(
        "2026-08-08",
        conn=FakeConn(),
    )

    assert result[2]["race_number"] == 1
    assert result[2]["fetched_at_label"] == "2026-08-08 12:00"


def test_venue_environment_falls_back_when_nearest_future_preview_is_empty(monkeypatch):
    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            rows = [
                (
                    "202608090211",
                    2,
                    11,
                    "2026-08-09T15:36:00+09:00",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "",
                    None,
                    None,
                    None,
                    0,
                    0,
                    None,
                ),
                (
                    "202608090212",
                    2,
                    12,
                    "2026-08-09T16:30:00+09:00",
                    "fresh",
                    1,
                    3.0,
                    4,
                    2.0,
                    "mid",
                    120.0,
                    5.0,
                    30.0,
                    0,
                    0,
                    "2026-08-09T14:55:00+09:00",
                ),
            ]
            return FakeCursor(rows)

    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-09")
    monkeypatch.setattr(
        web_app,
        "_now_jst",
        lambda: datetime(2026, 8, 9, 15, 20, tzinfo=web_app.JST),
    )

    result = web_app._venue_environment_summaries_for_date_impl(
        "2026-08-09",
        conn=FakeConn(),
    )

    assert result[2]["race_number"] == 12
    assert result[2]["weather_label"] != "天候 -"
    assert result[2]["wind_label"] != "風 -"
    assert result[2]["wave_label"] != "波 -"


def test_venue_environment_prefers_future_preview_data_over_tide_only_rows(monkeypatch):
    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            rows = [
                (
                    "202608091503",
                    15,
                    3,
                    "2026-08-09T16:13:00+09:00",
                    "fresh",
                    2,
                    2.0,
                    4,
                    2.0,
                    "rising",
                    120.0,
                    60.0,
                    30.0,
                    0,
                    0,
                    "2026-08-08T23:53:31+09:00",
                ),
                (
                    "202608091504",
                    15,
                    4,
                    "2026-08-09T16:43:00+09:00",
                    "fresh",
                    None,
                    None,
                    None,
                    None,
                    "rising",
                    120.0,
                    60.0,
                    30.0,
                    0,
                    0,
                    "2026-08-08T23:53:31+09:00",
                ),
            ]
            return FakeCursor(rows)

    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-09")
    monkeypatch.setattr(
        web_app,
        "_now_jst",
        lambda: datetime(2026, 8, 9, 16, 0, tzinfo=web_app.JST),
    )

    result = web_app._venue_environment_summaries_for_date_impl(
        "2026-08-09",
        conn=FakeConn(),
    )

    assert result[15]["race_number"] == 3
    assert result[15]["weather_label"] != "天候 -"
    assert result[15]["wind_label"] != "風 -"
    assert result[15]["wave_label"] != "波 -"


def test_race_detail_datetime_checks_use_jst_normalizer():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "closed_at = _parse_local_datetime(closed_at_raw)" in source
    assert "closed = _parse_local_datetime(race_closed_at)" in source
    assert "datetime.fromisoformat(str(race_closed_at).replace" not in source


def test_attach_race_detail_display_facts_skips_db_when_preds_are_already_complete(monkeypatch):
    preds = [
        {
            "boat_number": 1,
            "branch_number": 4123,
            "branch_label": "Tokyo",
            "age": 31,
            "weight": 52.0,
            "flying_count": 0,
            "late_count": 0,
            "national_top_1_percent": 7.1,
            "national_top_2_percent": 13.4,
            "local_top_2_percent": 18.2,
            "tilt_adjustment": -0.5,
            "avg_start_timing": 0.14,
            "dash_time": 6.72,
            "turn_time": 36.1,
            "straight_time": 7.11,
            "current_course_number": 1,
            "venue_recent10_course_win_starts": 8,
            "venue_recent10_course_win_rate": 37.5,
            "national_course_win_starts": 42,
            "national_course_win_rate": 45.2,
            "national_course_second_rate": 21.4,
            "national_course_top3_rate": 71.4,
        }
    ]

    def fail_db_connect():
        raise AssertionError("db_connect should not be used when facts are already present")

    monkeypatch.setattr(web_app, "db_connect", fail_db_connect)
    monkeypatch.setattr(
        web_app,
        "_original_exhibition_quality_marks",
        lambda race_ids: (_ for _ in ()).throw(
            AssertionError("quality marks should not be recomputed")
        ),
    )

    web_app._attach_race_detail_display_facts("20260804-01-01", preds)

    assert preds[0]["national_course_top3_rate"] == 71.4


def test_race_detail_serves_stale_cache_for_today_instead_of_live_recompute():
    """今日のレース詳細でも、fresh(180秒)が切れたら stale を即返し、
    13秒のライブ再計算をさせないこと (「レース詳細が開けない」対策)。"""
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def race_detail(race_id: str):")
    end = source.index("@app.route", start)
    route = source[start:end]
    # fresh miss + today のときに stale フォールバックする分岐
    assert "if not cached_html and use_fresh_page_cache:" in route
    assert "race_detail stale-cache served" in route


def _member_client(monkeypatch):
    monkeypatch.delenv("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    web_app._CACHE.clear()
    web_app._PAGE_HTML_MEM_CACHE.clear()
    app = web_app.create_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
    return client


def _fail_if_called(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"{name} must not be called")

    return fail


def test_human_cache_miss_returns_lightweight_preparing_page_without_recompute(
    monkeypatch,
):
    client = _member_client(monkeypatch)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-15")
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: None)
    monkeypatch.setattr(web_app, "_read_page_html_cache_stale", lambda *_args: None)
    for dependency in (
        "_race_basic_info",
        "_venue_environment_summaries_for_date",
        "_race_predictions_from_cache",
        "_race_entry_fallback_rows",
        "_race_current_conditions_cached",
        "db_connect",
    ):
        monkeypatch.setattr(web_app, dependency, _fail_if_called(dependency))

    response = client.get("/race/20260815-05-04?recompute=1")

    assert response.status_code == 200
    assert response.headers["Retry-After"] == "30"
    assert "no-store" in response.headers["Cache-Control"]
    body = response.get_data(as_text=True)
    assert "レース詳細を準備しています" in body
    assert "数十秒後に自動で更新されます" in body
    assert '<meta http-equiv="refresh" content="30">' in body


def test_preparing_response_is_not_held_by_view_ttl_cache(monkeypatch):
    client = _member_client(monkeypatch)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-15")
    page_reads = iter([None, "<main>prewarmed detail</main>"])
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache",
        lambda *_args: next(page_reads),
    )
    monkeypatch.setattr(web_app, "_read_page_html_cache_stale", lambda *_args: None)
    monkeypatch.setattr(web_app, "_race_basic_info", _fail_if_called("_race_basic_info"))

    first = client.get("/race/20260815-05-04")
    second = client.get("/race/20260815-05-04")

    assert "レース詳細を準備しています" in first.get_data(as_text=True)
    assert second.get_data(as_text=True) == "<main>prewarmed detail</main>"


@pytest.mark.parametrize("trigger", ["render-detail-prewarm", "render-cron"])
def test_approved_prewarm_trigger_keeps_live_generation_path(monkeypatch, trigger):
    client = _member_client(monkeypatch)
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", trigger)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-15")
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: None)
    monkeypatch.setattr(web_app, "_read_page_html_cache_stale", lambda *_args: None)
    basic_info_calls: list[str] = []

    def basic_info(race_id):
        basic_info_calls.append(race_id)
        return {
            "race_date": "2026-08-15",
            "stadium_number": 5,
            "race_closed_at": None,
        }

    monkeypatch.setattr(web_app, "_race_basic_info", basic_info)
    monkeypatch.setattr(web_app, "_venue_environment_summaries_for_date", lambda *_args: {})
    monkeypatch.setattr(web_app, "_race_predictions_from_cache", lambda *_args: [])
    monkeypatch.setattr(web_app, "_race_entry_fallback_rows", lambda *_args: [])
    monkeypatch.setattr(web_app, "_attach_race_detail_display_facts", lambda *_args: None)
    monkeypatch.setattr(web_app, "_attach_precomputed_race_detail_tags", lambda *_args: None)
    monkeypatch.setattr(web_app, "_attach_motor_fact_grades", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_app, "_race_current_conditions_cached", lambda *_args: {})
    monkeypatch.setattr(web_app, "_race_actual_result_cached", lambda *_args: None)
    monkeypatch.setattr(web_app, "_write_page_html_cache", lambda *_args: None)

    response = client.get("/race/20260815-05-04?recompute=1")

    assert response.status_code == 200
    assert basic_info_calls == ["20260815-05-04"]
    assert "レース詳細を準備しています" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("today", "fresh_html", "stale_html", "expected"),
    [
        ("2026-08-15", "<main>fresh</main>", None, "<main>fresh</main>"),
        ("2026-08-15", None, "<main>stale today</main>", "<main>stale today</main>"),
        ("2026-08-16", None, "<main>stale past</main>", "<main>stale past</main>"),
    ],
)
def test_race_detail_preserves_fresh_and_stale_cache_behavior(
    monkeypatch,
    today,
    fresh_html,
    stale_html,
    expected,
):
    client = _member_client(monkeypatch)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: today)
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: fresh_html)
    monkeypatch.setattr(web_app, "_read_page_html_cache_stale", lambda *_args: stale_html)
    monkeypatch.setattr(web_app, "_race_basic_info", _fail_if_called("_race_basic_info"))

    response = client.get("/race/20260815-05-04")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == expected
