from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_run_morning_orders_accident_before_predictions_and_skips_tags():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_morning(")
    end = src.index("def run_top_page_snapshot(", start)
    block = src[start:end]

    accident_idx = block.index('run_accident_self_heal(now)')
    entry_change_idx = block.index("run_entry_change_snapshot(today)")
    prediction_idx = block.index('run_py(["scripts/render_cache_predictions.py", "--date", today], timeout=1800)')

    assert accident_idx < entry_change_idx < prediction_idx
    assert 'prewarm_race_detail_tags.py' not in block


def test_signal_refresh_rebuilds_today_tags_before_today_pages():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_lite_daytime_bootstrap(")
    end = src.index("def tide_refresh_needed(", start)
    bootstrap = src[start:end]

    tags_idx = bootstrap.index('run_py(["scripts/prewarm_race_detail_tags.py", "--date", today], timeout=900)')
    pages_idx = bootstrap.index('run_py(["scripts/prewarm_race_detail_pages.py", "--date", today], timeout=1800)')

    assert tags_idx < pages_idx


def test_signal_refresh_runs_before_today_tag_materialization():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_lite_daytime_bootstrap(")
    end = src.index("def tide_refresh_needed(", start)
    bootstrap = src[start:end]

    source_idx = bootstrap.index('source_recovery_ok = task_success_exists("render_program_source_gate_v1", today)')
    signal_idx = bootstrap.index("ok = run_signal_refresh_slot(now, source_gate_verified=True)")
    tags_idx = bootstrap.index('run_py(["scripts/prewarm_race_detail_tags.py", "--date", today], timeout=900)')

    assert source_idx < signal_idx < tags_idx


def test_race_detail_cron_schedule_moves_after_final_source_recovery():
    src = (REPO / "render.yaml").read_text(encoding="utf-8")

    cron_idx = src.index("name: boatrace-race-detail-cron")
    schedule_idx = src.index('schedule: "*/15 0,22-23 * * *"', cron_idx)

    assert schedule_idx > cron_idx
