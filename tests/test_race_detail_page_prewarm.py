from __future__ import annotations

import builtins
import sys
from types import ModuleType
from datetime import datetime
from pathlib import Path

import pytest

from scripts import prewarm_race_detail_pages as page_prewarm
from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "web" / "app.py"
SCRIPT_SOURCE = ROOT / "scripts" / "prewarm_race_detail_pages.py"


class _SharedConnection:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        self.closed = True


def test_borrowed_prewarm_connection_cannot_close_loop_owner():
    owner = _SharedConnection()
    with web_app._use_race_detail_prewarm_context(owner, {}):
        borrowed = web_app.db_connect()
        borrowed.close()
        with borrowed:
            pass
    assert owner.closed is False


def test_race_detail_uses_fresh_page_cache_for_today_and_stale_for_past():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def race_detail(race_id: str):")
    end = source.index("@app.route", start)
    route_source = source[start:end]

    info_read = route_source.index("_race_basic_info(race_id)")
    # TTL は RACE_DETAIL_PAGE_FRESH_SEC に定数化した (2026-08-22)。
    # 直値 180 は展示cron(5分毎)より短く、常に stale 扱いになって
    # 閲覧のたびに再生成が走り本番が詰まった。
    fresh_cache_read = route_source.index("RACE_DETAIL_PAGE_FRESH_SEC")
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
    assert "cached_predictions_only=True" in source
    assert "elapsed_seconds" in source


def test_cached_only_app_does_not_import_or_construct_predictor(monkeypatch):
    fake_predictor_module = ModuleType("src.web.predictor")

    class ForbiddenPredictor:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("model predictor must not be constructed")

    fake_predictor_module.Predictor = ForbiddenPredictor
    monkeypatch.setitem(sys.modules, "src.web.predictor", fake_predictor_module)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "src.web.predictor":
            raise AssertionError("model predictor module must not be imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    app = web_app.create_app(cached_predictions_only=True)

    assert app is not None


def test_manual_prewarm_verifies_every_persistent_page_and_can_repair_only_missing():
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")

    assert "def _missing_persistent_page_ids" in source
    assert "persistent_cache_missing" in source
    assert 'parser.add_argument("--missing-only", action="store_true")' in source
    assert 'parser.add_argument("--retry-missing", type=int, default=1)' in source
    assert 'summary["requested_races"] > 0' in source
    assert page_prewarm.DEFAULT_BATCH_SIZE == 8


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
    monkeypatch.setattr(page_prewarm, "db_connect", _SharedConnection)
    monkeypatch.setattr(page_prewarm, "_race_ids", lambda *_args, **_kwargs: ["race-1", "race-2"])
    monkeypatch.setattr(page_prewarm, "_missing_persistent_page_ids", lambda _ids, **_kwargs: next(checks))
    monkeypatch.setattr(page_prewarm.web_app, "_prefetch_race_detail_page_inputs", lambda *_args: {})
    monkeypatch.setattr(page_prewarm.web_app, "create_app", lambda **_kwargs: App())

    summary = page_prewarm.prewarm("2026-08-11")

    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["persistent_missing"] == 1
    assert summary["failures"] == [
        {"race_id": "race-2", "status": "persistent_cache_missing"}
    ]


def test_page_prewarm_budget_saves_each_page_and_resumes_missing(monkeypatch):
    cached = {"race-1"}
    clock = {"value": 0.0}

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

        def get(self, url):
            if "?recompute=1" in url:
                cached.add(url.split("/race/", 1)[1].split("?", 1)[0])
                clock["value"] += 2.0
            return Response()

    class App:
        testing = False

        def test_client(self):
            return Client()

    monkeypatch.setattr(page_prewarm, "_require_postgres", lambda: None)
    monkeypatch.setattr(page_prewarm, "db_connect", _SharedConnection)
    monkeypatch.setattr(page_prewarm, "_race_ids", lambda *_args, **_kwargs: ["race-1", "race-2", "race-3"])
    monkeypatch.setattr(
        page_prewarm,
        "_missing_persistent_page_ids",
        lambda race_ids, **_kwargs: [race_id for race_id in race_ids if race_id not in cached],
    )
    monkeypatch.setattr(page_prewarm.web_app, "_prefetch_race_detail_page_inputs", lambda *_args: {})
    monkeypatch.setattr(page_prewarm.time, "perf_counter", lambda: clock["value"])
    monkeypatch.setattr(page_prewarm.web_app, "create_app", lambda **_kwargs: App())

    first = page_prewarm.prewarm(
        "2026-08-16", missing_only=True, retry_missing=0, budget_sec=1
    )
    assert first["skipped_existing"] == 1
    assert first["succeeded"] == 1
    assert first["remaining"] == 1
    assert first["budget_exhausted"] is True
    assert cached == {"race-1", "race-2"}

    second = page_prewarm.prewarm(
        "2026-08-16", missing_only=True, retry_missing=0
    )
    assert second["skipped_existing"] == 2
    assert second["succeeded"] == 1
    assert second["remaining"] == 0
    assert cached == {"race-1", "race-2", "race-3"}


def test_page_prewarm_closes_each_sub_batch_and_collects_garbage(monkeypatch):
    connections = []
    prefetched_batches = []
    gc_calls = []
    response_closes = []

    class Connection(_SharedConnection):
        def __enter__(self):
            connections.append(self)
            return self

        def __exit__(self, *_args):
            self.closed = True
            return False

    class Session:
        def __enter__(self):
            return {}

        def __exit__(self, *_args):
            return False

    class Response:
        status_code = 200
        data = b"stable-html"

        def close(self):
            response_closes.append(True)

    class Client:
        def session_transaction(self):
            return Session()

        def get(self, _url):
            return Response()

    class App:
        testing = False

        def test_client(self):
            return Client()

    race_ids = [f"race-{index}" for index in range(5)]
    monkeypatch.setattr(page_prewarm, "_require_postgres", lambda: None)
    monkeypatch.setattr(page_prewarm, "db_connect", Connection)
    monkeypatch.setattr(page_prewarm, "_race_ids", lambda *_args, **_kwargs: race_ids)
    monkeypatch.setattr(page_prewarm, "_missing_persistent_page_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        page_prewarm.web_app,
        "_prefetch_race_detail_page_inputs",
        lambda ids, *_args: prefetched_batches.append(list(ids)) or {},
    )
    monkeypatch.setattr(page_prewarm.web_app, "create_app", lambda **_kwargs: App())
    monkeypatch.setattr(page_prewarm.gc, "collect", lambda: gc_calls.append(True) or 0)

    summary = page_prewarm.prewarm("2026-08-17", batch_size=2)

    assert prefetched_batches == [race_ids[:2], race_ids[2:4], race_ids[4:]]
    assert summary["batches"] == 3
    assert summary["batch_size"] == 2
    assert summary["succeeded"] == 5
    assert all(connection.closed for connection in connections)
    assert len(gc_calls) == 3
    assert len(response_closes) == 8  # five builds plus three cache-read samples


def test_page_html_is_byte_identical_with_shared_prefetch_context(monkeypatch):
    race_id = "20260816-01-01"
    info = {
        "race_id": race_id,
        "race_date": "2026-08-16",
        "stadium_number": 1,
        "stadium_name": "桐生",
        "race_number": 1,
        "race_grade_number": 5,
        "race_title": "固定テスト",
        "race_subtitle": "",
        "race_closed_at": "2026-08-16T23:00:00+09:00",
        "boatcast_replay_url": "",
    }
    preds = [
        {
            "boat_number": 1,
            "racer_name": "固定選手",
            "prob_first": 0.71,
            "prob_top_2": 0.82,
            "prob_top_3": 0.91,
            "national_top_1_percent": None,
            "national_top_2_percent": None,
            "local_top_2_percent": None,
            "assigned_motor_top_2_percent": None,
            "avg_start_timing": None,
            "dash_time": None,
            "exhibition_time": None,
            "national_course_second_rate": None,
            "national_course_top3_rate": None,
            "national_course_win_rate": None,
            "start_timing_exhibition": None,
            "straight_time": None,
            "tilt_adjustment": None,
            "turn_time": None,
            "venue_recent10_course_win_rate": None,
            "weight": None,
            "finishing_position": None,
            "racer_number": 1001,
            "class_number": 1,
            "branch_number": 1,
            "branch_label": "支部1",
            "age": 30,
            "flying_count": 0,
            "late_count": 0,
            "assigned_motor_number": 1,
            "pred_rank": 1,
        }
    ]
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-detail-prewarm")
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-16")
    monkeypatch.setattr(
        web_app,
        "_now_jst",
        lambda: datetime.fromisoformat("2026-08-16T06:30:00+09:00"),
    )
    monkeypatch.setattr(web_app, "_race_basic_info", lambda _rid: dict(info))
    monkeypatch.setattr(web_app, "_venue_environment_summaries_for_date", lambda *_args: {})
    monkeypatch.setattr(web_app, "_race_predictions_from_cache", lambda *_args: [dict(row) for row in preds])
    monkeypatch.setattr(web_app, "_race_entry_fallback_rows", lambda *_args: [])
    monkeypatch.setattr(web_app, "_attach_race_detail_display_facts", lambda *_args: None)
    monkeypatch.setattr(web_app, "_attach_precomputed_race_detail_tags", lambda *_args: None)
    monkeypatch.setattr(web_app, "_attach_motor_fact_grades", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_app, "_race_current_conditions_cached", lambda *_args: {})
    monkeypatch.setattr(web_app, "_write_page_html_cache", lambda *_args: None)

    def render_bytes(prefetched=None, *, cached_only=False):
        web_app._CACHE.clear()
        web_app._PAGE_HTML_MEM_CACHE.clear()
        app = web_app.create_app(cached_predictions_only=cached_only)
        app.testing = True
        client = app.test_client()
        with client.session_transaction() as session:
            session["is_member"] = True
        if prefetched is None:
            return client.get(f"/race/{race_id}?recompute=1").data
        with web_app._use_race_detail_prewarm_context(_SharedConnection(), prefetched):
            return client.get(f"/race/{race_id}?recompute=1").data

    assert render_bytes(
        {"race_info": {race_id: info}},
        cached_only=True,
    ) == render_bytes()


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
    assert "_start_race_detail_background_refresh(" in route
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


def test_human_cache_miss_keeps_synchronous_generation(monkeypatch):
    client = _member_client(monkeypatch)
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

    response = client.get("/race/20260815-05-04")

    assert response.status_code == 200
    assert basic_info_calls == ["20260815-05-04"]
    assert "Retry-After" not in response.headers


def test_background_refresh_guard_prevents_duplicate_start(monkeypatch):
    pending: list[tuple[object, tuple[object, ...]]] = []
    rebuilt: list[str] = []

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs):
            pending.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(web_app.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        web_app,
        "_rebuild_race_detail_page_in_background",
        lambda _app, race_id: rebuilt.append(race_id)
        or web_app._RACE_DETAIL_REFRESH_IN_FLIGHT.discard(race_id),
    )
    web_app._RACE_DETAIL_REFRESH_IN_FLIGHT.clear()
    app = object()

    assert web_app._start_race_detail_background_refresh(app, "race-1") is True
    assert web_app._start_race_detail_background_refresh(app, "race-1") is False
    assert len(pending) == 1
    target, args = pending.pop()
    target(*args)
    assert rebuilt == ["race-1"]
    assert web_app._start_race_detail_background_refresh(app, "race-1") is True
    target, args = pending.pop()
    target(*args)
    assert rebuilt == ["race-1", "race-1"]


def test_background_refresh_uses_canonical_recompute_route_and_clears_guard():
    calls: list[str] = []
    session_data: dict[str, object] = {}

    class Response:
        status_code = 200

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def session_transaction(self):
            return self

        def __setitem__(self, key, value):
            session_data[key] = value

        def get(self, path):
            assert web_app._RACE_DETAIL_BACKGROUND_RECOMPUTE.get() is True
            calls.append(path)
            return Response()

    class App:
        def test_client(self):
            return Client()

    web_app._RACE_DETAIL_REFRESH_IN_FLIGHT.add("race-2")
    web_app._rebuild_race_detail_page_in_background(App(), "race-2")

    assert calls == ["/race/race-2?recompute=1"]
    assert session_data == {"is_member": True}
    assert web_app._RACE_DETAIL_BACKGROUND_RECOMPUTE.get() is False
    assert "race-2" not in web_app._RACE_DETAIL_REFRESH_IN_FLIGHT


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
    ("today", "fresh_html", "stale_html", "expected", "refresh_expected"),
    [
        ("2026-08-15", "<main>fresh</main>", None, "<main>fresh</main>", False),
        ("2026-08-15", None, "<main>stale today</main>", "stale today", True),
        ("2026-08-16", None, "<main>stale past</main>", "<main>stale past</main>", False),
    ],
)
def test_race_detail_preserves_fresh_and_stale_cache_behavior(
    monkeypatch,
    today,
    fresh_html,
    stale_html,
    expected,
    refresh_expected,
):
    client = _member_client(monkeypatch)
    refreshes: list[str] = []
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: today)
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: fresh_html)
    monkeypatch.setattr(web_app, "_read_page_html_cache_stale", lambda *_args: stale_html)
    monkeypatch.setattr(
        web_app,
        "_start_race_detail_background_refresh",
        lambda _app, race_id: refreshes.append(race_id) or True,
    )
    monkeypatch.setattr(web_app, "_race_basic_info", _fail_if_called("_race_basic_info"))

    response = client.get("/race/20260815-05-04")

    assert response.status_code == 200
    assert expected in response.get_data(as_text=True)
    assert bool(refreshes) is refresh_expected
    assert (response.headers.get("X-Boatrace-Data-Stale") == "1") is refresh_expected


def test_prewarm_disables_maintenance_gate_for_its_own_requests(monkeypatch):
    """メンテ窓 (04:00-07:00 JST) の中でも prewarm 自身は 503 を受けない。

    2026-08-16 以降の実障害: prewarm は 05:31 に窓の中で走るため、自分の
    test_client リクエストが before_request でメンテ画面 (503) に差し替えられ、
    ルートが一度も実行されずページが 1 枚も保存されないまま failed=N で
    終わっていた。preflight の _probe_today_races_page と同じ自衛策が
    prewarm 側に無かったのが原因。
    """
    import os

    import scripts.prewarm_race_detail_pages as prewarm_module

    monkeypatch.setenv("BOATRACE_MAINTENANCE_WINDOW", "1")
    seen = {}
    with prewarm_module._maintenance_gate_disabled():
        seen["inside"] = os.environ.get("BOATRACE_MAINTENANCE_WINDOW")
    seen["after"] = os.environ.get("BOATRACE_MAINTENANCE_WINDOW")

    assert seen["inside"] == "0", "生成中は窓を無効にする"
    assert seen["after"] == "1", "抜けたら元に戻す"


def test_prewarm_wraps_page_generation_in_the_gate_bypass():
    """自衛策の呼び出しが生成経路から消えたら気付けるようにする。"""
    from pathlib import Path

    source = Path("scripts/prewarm_race_detail_pages.py").read_text(encoding="utf-8")
    assert "_maintenance_gate_disabled()" in source
    gate_at = source.index("gate = _maintenance_gate_disabled()")
    create_at = source.index("app = web_app.create_app(")
    assert gate_at < create_at, "create_app より前に窓を無効化すること"


def test_detail_page_ttl_is_longer_than_exhibition_cron_interval():
    """詳細ページの鮮度TTLが展示cronの間隔より十分長いこと。

    2026-08-22 実障害: TTL が 180 秒だったため、展示 cron
    (refresh_race_detail_after_exhibition, 5分毎) が作り直した 3 分後には
    全ページが stale 扱いになり、閲覧のたびに 15-25 秒の再生成が裏で走った。
    連続アクセスでワーカーが詰まり本番が 502 になった。
    鮮度は cron が実データの更新を見て担保しているので TTL を重ねる必要はない。
    """
    from src.web import app as web_app

    assert web_app.RACE_DETAIL_PAGE_FRESH_SEC >= 900, (
        "展示cronの5分間隔より十分長いこと"
    )
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    assert "_read_page_html_cache(page_cache_key, 180)" not in source, (
        "180秒の直値に戻さないこと"
    )
