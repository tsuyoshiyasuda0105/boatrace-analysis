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


def test_maintenance_rebuilds_today_tags_before_today_pages():
    src = (REPO / "scripts" / "render_maintenance_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_detail_phase(")
    end = src.index("def run_snapshot_phase(", start)
    bootstrap = src[start:end]

    tags_idx = bootstrap.index('"scripts/prewarm_race_detail_tags.py"')
    pages_idx = bootstrap.index('"scripts/prewarm_race_detail_pages.py"')

    assert tags_idx < pages_idx


def test_daytime_bootstrap_keeps_full_detail_materialization_out():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_lite_daytime_bootstrap(")
    end = src.index("def tide_refresh_needed(", start)
    bootstrap = src[start:end]

    source_idx = bootstrap.index('source_recovery_ok = task_success_exists("render_program_source_gate_v1", today)')
    signal_idx = bootstrap.index("ok = run_signal_refresh_slot(now, source_gate_verified=True)")

    assert source_idx < signal_idx
    assert "prewarm_race_detail_tags.py" not in bootstrap
    assert "prewarm_race_detail_pages.py" not in bootstrap


def test_race_detail_cron_is_the_overnight_maintenance_coordinator():
    src = (REPO / "render.yaml").read_text(encoding="utf-8")

    cron_idx = src.index("name: boatrace-race-detail-cron")
    schedule_idx = src.index('schedule: "*/10 19-21 * * *"', cron_idx)
    command_idx = src.index("python scripts/render_maintenance_scheduler.py", cron_idx)

    assert schedule_idx > cron_idx
    assert command_idx > schedule_idx
