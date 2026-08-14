from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_regular_scheduler_no_longer_owns_the_morning_pipeline():
    regular = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    maintenance = (REPO / "scripts" / "render_maintenance_scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "def run_morning(" not in regular
    assert "def run_program_phase(" in maintenance
    assert "def run_accident_phase(" in maintenance


def test_maintenance_rebuilds_today_tags_before_today_pages():
    src = (REPO / "scripts" / "render_maintenance_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_detail_phase(")
    end = src.index("def run_snapshot_phase(", start)
    bootstrap = src[start:end]

    tags_idx = bootstrap.index('"scripts/prewarm_race_detail_tags.py"')
    pages_idx = bootstrap.index('"scripts/prewarm_race_detail_pages.py"')

    assert tags_idx < pages_idx


def test_daytime_bootstrap_runs_bounded_detail_selfheal_after_source_gate():
    src = (REPO / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    start = src.index("def run_lite_daytime_bootstrap(")
    end = src.index("def tide_refresh_needed(", start)
    bootstrap = src[start:end]

    source_idx = bootstrap.index('source_recovery_ok = task_success_exists("render_program_source_gate_v1", today)')
    signal_idx = bootstrap.index("ok = run_signal_refresh_slot(now, source_gate_verified=True)")
    selfheal_idx = bootstrap.index("detail_selfheal_ok = run_detail_pages_selfheal(now)")

    assert source_idx < signal_idx < selfheal_idx


def test_race_detail_cron_is_the_overnight_maintenance_coordinator():
    src = (REPO / "render.yaml").read_text(encoding="utf-8")

    cron_idx = src.index("name: boatrace-race-detail-cron")
    schedule_idx = src.index('schedule: "*/10 19-21 * * *"', cron_idx)
    command_idx = src.index("python scripts/render_maintenance_scheduler.py", cron_idx)

    assert schedule_idx > cron_idx
    assert command_idx > schedule_idx
