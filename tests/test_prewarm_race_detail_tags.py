from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prewarm_race_detail_tags.py"
SCHEDULER = ROOT / "scripts" / "render_regular_scheduler.py"


def test_daily_tag_prewarm_is_wired_into_morning_and_nightly_jobs():
    source = SCHEDULER.read_text(encoding="utf-8")

    morning = source.split("def run_morning", 1)[1].split("def tide_refresh_needed", 1)[0]
    nightly = source.split("def run_nightly", 1)[1].split("def main", 1)[0]
    assert '"scripts/prewarm_race_detail_tags.py", "--date", today' in morning
    assert '"scripts/prewarm_race_detail_tags.py", "--date", tomorrow' in nightly
    assert "hour=6, minute=0" in source


def test_tag_prewarm_covers_every_race_and_forces_snapshot_refresh():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "WHERE race_date = ?" in source
    assert "_race_detail_tag_snapshot(str(race_id), recompute=True)" in source


def test_escape_tag_uses_monthly_frozen_boat1_profile():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")

    assert 'RACE_DETAIL_TAG_CACHE_VERSION = "v5"' in source
    assert "def _boat1_monthly_escape_profile" in source
    assert "def _monthly_snapshot_window" in source
    assert "WHERE race_id = ? AND boat_number = 1" in source
    assert "COALESCE(NULLIF(rr1.course_number, 0), e1.boat_number) = 1" in source
    assert "escape_rate >= 70.0" in source
    assert '"snapshot_month": str(boat1_escape.get("snapshot_month") or "")' in source
    assert "escape_context_tag" in source
    assert "preferred_course" in source
