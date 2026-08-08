from __future__ import annotations

from pathlib import Path

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "web" / "app.py"
SCRIPT_SOURCE = ROOT / "scripts" / "prewarm_race_detail_pages.py"


def test_race_detail_serves_complete_page_cache_before_db_detail_work():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def race_detail(race_id: str):")
    end = source.index("@app.route", start)
    route_source = source[start:end]

    cache_read = route_source.index("_read_page_html_cache_stale(page_cache_key)")
    info_read = route_source.index("_race_basic_info(race_id)")
    assert cache_read < info_read
    assert "_write_page_html_cache(page_cache_key, html)" in route_source


def test_manual_prewarm_uses_guarded_recompute_and_member_session():
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")

    assert 'BOATRACE_TASK_TRIGGER", "render-detail-prewarm"' in source
    assert 'sess["is_member"] = True' in source
    assert 'client.get(f"/race/{rid}?recompute=1")' in source
    assert "elapsed_seconds" in source


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
