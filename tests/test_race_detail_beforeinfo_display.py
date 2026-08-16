from __future__ import annotations

from scripts import refresh_race_detail_after_exhibition as exhibition_refresh
from src.web import app as web_app


RACE_ID = "20260816-02-02"


def _toda2_conditions() -> dict:
    times = [6.66, 6.64, 6.73, 6.70, 6.83, 6.72]
    return {
        "weather_number": 2,
        "temperature": 27.0,
        "water_temperature": 27.0,
        "wind_speed": 1,
        "wind_direction_number": 3,
        "wave_height": 1,
        "boats": {
            boat: {
                "course_number": boat,
                "start_timing_exhibition": 0.10 + boat / 100,
                "exhibition_time": times[boat - 1],
                "tilt_adjustment": 0.0,
                "stable_plate": 1 if boat == 6 else 0,
            }
            for boat in range(1, 7)
        },
    }


def _member_client(monkeypatch):
    web_app._CACHE.clear()
    web_app._PAGE_HTML_MEM_CACHE.clear()
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-detail-prewarm")
    monkeypatch.delenv("BOATRACE_MAINTENANCE_WINDOW", raising=False)
    app = web_app.create_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
    return client


def _patch_lightweight_detail_dependencies(monkeypatch, conditions, writes):
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-16")
    monkeypatch.setattr(
        web_app,
        "_race_basic_info",
        lambda race_id: {
            "race_id": race_id,
            "race_date": "2026-08-16",
            "stadium_number": 2,
            "stadium_name": "戸田",
            "race_number": 2,
            "race_closed_at": "2026-08-16 11:21:00",
            "race_title": "一般",
            "race_subtitle": None,
            "boatcast_replay_url": "https://example.invalid/replay",
        },
    )
    monkeypatch.setattr(web_app, "_venue_environment_summaries_for_date", lambda *_args: {})
    monkeypatch.setattr(web_app, "_race_predictions_from_cache", lambda *_args: [])
    monkeypatch.setattr(web_app, "_race_entry_fallback_rows", lambda *_args: [])
    monkeypatch.setattr(web_app, "_attach_race_detail_display_facts", lambda *_args: None)
    monkeypatch.setattr(web_app, "_attach_precomputed_race_detail_tags", lambda *_args: None)
    monkeypatch.setattr(web_app, "_attach_motor_fact_grades", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_app, "_race_current_conditions_cached", lambda *_args: conditions)
    monkeypatch.setattr(web_app, "_race_actual_result_cached", lambda *_args: None)
    monkeypatch.setattr(web_app, "_write_page_html_cache", lambda key, html: writes.update({key: html}))


def test_toda2_beforeinfo_fixture_builds_every_official_display_field():
    display = web_app._race_beforeinfo_display(
        {"stadium_number": 2},
        _toda2_conditions(),
    )

    assert display is not None
    assert display["weather_label"] == "曇り"
    assert display["temperature_label"] == "27℃"
    assert display["water_temperature_label"] == "27℃"
    assert display["wind_speed_label"] == "1m"
    assert display["wind_direction_label"] == "北東"
    assert display["wave_height_label"] == "1cm"
    assert [row["exhibition_time"] for row in display["boats"]] == [
        6.66, 6.64, 6.73, 6.70, 6.83, 6.72,
    ]
    assert display["boats"][5]["stable_plate_label"] == "安定板"


def test_unpublished_beforeinfo_stays_empty_without_error():
    assert web_app._race_beforeinfo_display({"stadium_number": 2}, {"boats": {}}) is None


def test_forced_cache_regeneration_replaces_old_html_with_complete_toda2_beforeinfo(monkeypatch):
    writes: dict[str, str] = {}
    conditions = _toda2_conditions()
    client = _member_client(monkeypatch)
    _patch_lightweight_detail_dependencies(monkeypatch, conditions, writes)
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda *_args: "<main>old cache: 展示のみ</main>",
    )

    response = client.get(f"/race/{RACE_ID}?recompute=1")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for expected in (
        "直前情報", "曇り", "27℃", "1m", "北東", "1cm",
        "6.66", "6.64", "6.73", "6.70", "6.83", "6.72",
        "展示ST", "チルト", "安定板",
    ):
        assert expected in html
    assert "old cache: 展示のみ" not in html
    cache_key = web_app._race_detail_page_cache_key(RACE_ID)
    assert writes[cache_key] == html


def test_unpublished_detail_renders_without_beforeinfo_panel(monkeypatch):
    writes: dict[str, str] = {}
    client = _member_client(monkeypatch)
    _patch_lightweight_detail_dependencies(monkeypatch, {"boats": {}}, writes)

    response = client.get(f"/race/{RACE_ID}?recompute=1")

    assert response.status_code == 200
    assert 'class="beforeinfo-panel"' not in response.get_data(as_text=True)


def test_exhibition_cron_regenerates_complete_page_after_new_preview_arrives(monkeypatch):
    writes: dict[str, str] = {}
    json_writes: dict[str, dict] = {}
    conditions = _toda2_conditions()
    invalidations: list[bool] = []
    _patch_lightweight_detail_dependencies(monkeypatch, conditions, writes)
    monkeypatch.setenv("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1")
    monkeypatch.setattr(exhibition_refresh, "_due_races", lambda *_args, **_kwargs: [RACE_ID])
    monkeypatch.setattr(web_app, "_race_current_conditions", lambda *_args: conditions)
    monkeypatch.setattr(web_app, "_write_json_cache", lambda key, value: json_writes.update({key: value}))
    monkeypatch.setattr(web_app, "_motor_history_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_app, "invalidate_cache", lambda: invalidations.append(True))

    summary = exhibition_refresh.refresh("2026-08-16", delay_seconds=60, limit=12)

    assert summary["refreshed"] == 1
    assert summary["failed"] == 0
    assert invalidations == [True]
    assert json_writes[f"race_conditions:{RACE_ID}"] == conditions
    html = writes[web_app._race_detail_page_cache_key(RACE_ID)]
    assert all(value in html for value in ("曇り", "27℃", "1m", "1cm", "6.66", "6.72"))


def test_invalidate_cache_clears_venue_environment_memoization(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        web_app,
        "_venue_environment_summaries_for_date_impl",
        lambda target_date: calls.append(target_date) or {},
    )
    web_app._venue_environment_summaries_for_date_cached.cache_clear()

    web_app._venue_environment_summaries_for_date("2099-01-01")
    web_app._venue_environment_summaries_for_date("2099-01-01")
    web_app.invalidate_cache()
    web_app._venue_environment_summaries_for_date("2099-01-01")

    assert calls == ["2099-01-01", "2099-01-01"]
