from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_run_morning_orders_accident_before_predictions_and_skips_tags():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_morning(")
    end = src.index("def tide_refresh_needed(", start)
    block = src[start:end]

    accident_idx = block.index('run_accident_self_heal(now)')
    prediction_idx = block.index('run_py(["scripts/render_cache_predictions.py", "--date", today], timeout=1800)')

    assert accident_idx < prediction_idx
    assert 'prewarm_race_detail_tags.py' not in block


def test_signal_refresh_runs_before_today_tag_materialization():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")

    signal_idx = src.index("signal_ok = run_signal_refresh_slot(now)")
    tags_idx = src.index('run_py(["scripts/prewarm_race_detail_tags.py", "--date", today], timeout=900)')

    assert signal_idx < tags_idx


def test_race_detail_cron_schedule_moves_after_morning_refresh():
    src = (REPO / "render.yaml").read_text(encoding="utf-8")

    cron_idx = src.index("name: boatrace-race-detail-cron")
    schedule_idx = src.index('schedule: "45 21 * * *"', cron_idx)

    assert schedule_idx > cron_idx
