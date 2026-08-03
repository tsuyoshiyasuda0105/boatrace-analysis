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
    assert "def collect_live_exhibition" in source
    assert "live_beforeinfo.find_due_races" in source
    assert "live_beforeinfo.find_recent_incomplete_races" in source
    assert "original_exhibition_collector.collect_for_races" in source
    assert '["scripts/render_cache_predictions.py", "--date", target_date]' in source
    assert '["scripts/generate_start_predictions.py", "--date", target_date]' in source
    assert "page_ts < source_ts" in source
    assert "CAST(MAX(c.updated_at) AS DOUBLE PRECISION)" in source
    due_races = source.split("def _due_races", 1)[1].split("def refresh", 1)[0]
    assert "from src.web import app as web_app" in due_races
    assert due_races.index("from src.web import app as web_app") < due_races.index(
        'page_cache_prefix = web_app._race_detail_page_cache_key("")'
    )
    assert 'page_cache_prefix = web_app._race_detail_page_cache_key("")' in source
    assert "'race_detail_page:v1:' || r.race_id" not in source
    assert 'datetime.now(timezone.utc).isoformat(timespec="seconds")' in source
    assert 'client.get(f"/race/{race_id}?recompute=1")' in source
    assert "_motor_history_payload(race_id, boat" in source
    assert "web_app.invalidate_cache()" in source
    assert "_clear_web_caches" not in source
    assert 'BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1"' in source
    assert '["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", target_date]' in source
    assert "task_name LIKE 'render_signal_refresh_%'" in source
    assert "SIGNAL_REFRESH_MIN_GAP_MIN = 2" in source
    assert "EXHIBITION_REFRESH_MAX_ACTIVE_MIN = 15" in source
    assert "_exhibition_refresh_recently_running(args.date, now)" in source
    assert 'task_name = \'render_exhibition_detail_refresh\'' in source


def test_exhibition_refresh_imports_web_app_after_collection_path():
    source = (ROOT / "scripts" / "refresh_race_detail_after_exhibition.py").read_text(encoding="utf-8")

    imports = source.split("def collect_live_exhibition", 1)[0]
    refresh = source.split("def refresh", 1)[1].split("def main", 1)[0]
    assert "from src.web import app as web_app" not in imports
    assert "from src.web import app as web_app" in refresh


def test_web_app_keeps_legacy_clear_cache_hook_for_cron_rollouts():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")

    assert "def _clear_web_caches()" in source
    assert "invalidate_cache()" in source.split("def _clear_web_caches()", 1)[1].split(
        "def _ensure_page_html_cache_table", 1
    )[0]


def test_motor_history_can_be_run_alone_with_bounded_parallelism():
    source = (ROOT / "scripts" / "prewarm_race_detail_data.py").read_text(encoding="utf-8")
    refresh_source = (ROOT / "scripts" / "refresh_race_detail_after_exhibition.py").read_text(encoding="utf-8")

    assert 'choices=("all", "motor")' in source
    assert "ThreadPoolExecutor(max_workers=max(1, workers))" in source
    assert 'MOTOR_CACHE_VERSION = "v9"' in source
    assert 'MOTOR_CACHE_VERSION = "v9"' in refresh_source
    assert 'BOATRACE_MOTOR_PREWARM_WORKERS", "4"' in source
    assert 'BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1"' in source


def test_render_blueprint_separates_daily_and_exhibition_jobs():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "name: boatrace-race-detail-cron" in source
    assert 'schedule: "0 22 * * *"' in source
    assert "python scripts/prewarm_race_detail_data.py" in source
    assert "name: boatrace-exhibition-detail-cron" in source
    assert 'schedule: "*/2 * * * *"' in source
    assert "python scripts/refresh_race_detail_after_exhibition.py" in source


def test_dedicated_detail_crons_persist_health_records():
    daily = (ROOT / "scripts" / "prewarm_race_detail_data.py").read_text(encoding="utf-8")
    exhibition = (ROOT / "scripts" / "refresh_race_detail_after_exhibition.py").read_text(
        encoding="utf-8"
    )

    assert 'record_cron_run(task_name, args.date, "running")' in daily
    assert '"success" if succeeded else "failure"' in daily
    assert 'record_cron_run(task_name, args.date, "running")' in exhibition
    assert '"success" if succeeded else "failure"' in exhibition
    assert '"signal_refresh_triggered"' in exhibition
    assert '"signal_refresh_ok"' in exhibition
    assert '_record_task(task_name, args.date, "running")' in exhibition


def test_regular_scheduler_no_longer_collects_exhibition_data():
    source = (ROOT / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    main = source.split("def main() -> int:", 1)[1]

    assert "run_beforeinfo(now)" not in main
    assert "run_original_exhibition_catchup" not in main
    assert "generate_start_predictions.py" not in main
    assert "owned by boatrace-exhibition-detail-cron" in main


def test_ace_kimarite_query_uses_race_entries_racer_number():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    start = source.index("def _historical_attack_kimarite_stats")
    end = source.index("def _match_ace_kimarite_win_strategies", start)
    query = source[start:end]

    assert "WHERE re.racer_number = ?" in query
    assert "rr.racer_number" not in query
