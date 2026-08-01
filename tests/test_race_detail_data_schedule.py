from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_detail_data_is_built_in_requested_order():
    source = (ROOT / "scripts" / "prewarm_race_detail_data.py").read_text(encoding="utf-8")

    racer = source.index("# Player data is stable for the day and is built first.")
    motor = source.index("# Motor history follows player data, as requested.")
    tags = source.index("# Tags must exist before complete HTML is rendered.")
    pages = source.index("page_summary = prewarm_pages(target_date)")
    assert racer < motor < tags < pages


def test_exhibition_refresh_waits_one_minute_and_is_targeted():
    source = (ROOT / "scripts" / "refresh_race_detail_after_exhibition.py").read_text(encoding="utf-8")

    assert "delay_seconds: int = 60" in source
    assert "page_ts < source_ts" in source
    assert 'client.get(f"/race/{race_id}?recompute=1")' in source
    assert "_motor_history_payload(race_id, boat" in source


def test_render_blueprint_separates_daily_and_exhibition_jobs():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "name: boatrace-race-detail-cron" in source
    assert 'schedule: "0 0 * * *"' in source
    assert "python scripts/prewarm_race_detail_data.py" in source
    assert "name: boatrace-exhibition-detail-cron" in source
    assert 'schedule: "* * * * *"' in source
    assert "python scripts/refresh_race_detail_after_exhibition.py" in source


def test_ace_kimarite_query_uses_race_entries_racer_number():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    start = source.index("def _historical_attack_kimarite_stats")
    end = source.index("def _match_ace_kimarite_win_strategies", start)
    query = source[start:end]

    assert "WHERE re.racer_number = ?" in query
    assert "rr.racer_number" not in query
