from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prewarm_race_detail_tags.py"
SCHEDULER = ROOT / "scripts" / "render_regular_scheduler.py"
PROGRAM_BOOTSTRAP = ROOT / "scripts" / "render_program_bootstrap_scheduler.py"
MAINTENANCE = ROOT / "scripts" / "render_maintenance_scheduler.py"


def test_daily_tag_prewarm_is_owned_by_maintenance_not_regular_cron():
    source = SCHEDULER.read_text(encoding="utf-8")

    bootstrap = source.split("def run_lite_daytime_bootstrap", 1)[1].split("def tide_refresh_needed", 1)[0]
    maintenance = MAINTENANCE.read_text(encoding="utf-8")
    assert '"scripts/prewarm_race_detail_tags.py", "--date", today' not in bootstrap
    assert '["scripts/prewarm_race_detail_tags.py", "--date", today]' in maintenance
    assert "def run_nightly(" not in source
    assert '["scripts/prewarm_race_detail_pages.py", "--date", today]' in maintenance
    assert maintenance.index('["scripts/prewarm_race_detail_tags.py", "--date", today]') < maintenance.index('["scripts/prewarm_race_detail_pages.py", "--date", today]')
    assert "_at_or_after(now, 6, 30)" in PROGRAM_BOOTSTRAP.read_text(encoding="utf-8")


def test_entry_change_snapshot_records_task_runs():
    source = SCHEDULER.read_text(encoding="utf-8")
    block = source.split("def run_entry_change_snapshot", 1)[1].split("def task_success_exists", 1)[0]

    assert 'record_task("render_entry_change_snapshot", target_date, "success", detail="skip:no-races")' in block
    assert '"render_entry_change_snapshot"' in block
    assert '"success" if verified else "failure"' in block


def test_tag_prewarm_covers_every_race_and_forces_snapshot_refresh():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "WHERE race_date = ?" in source
    assert "_race_detail_tag_snapshot(str(race_id), recompute=True)" in source


def test_escape_tag_uses_monthly_frozen_boat1_profile():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")

    assert 'RACE_DETAIL_TAG_CACHE_VERSION = "v6"' in source
    assert "def _boat1_monthly_escape_profile" in source
    assert "def _monthly_snapshot_window" in source
    assert "WHERE race_id = ? AND boat_number = 1" in source
    assert "COALESCE(NULLIF(rr1.course_number, 0), e1.boat_number) = 1" in source
    assert "escape_rate >= 70.0" in source
    assert '"snapshot_month": str(boat1_escape.get("snapshot_month") or "")' in source
    assert "escape_context_tag" in source
    assert "preferred_course" in source
    assert "entry_change_tag" in source
